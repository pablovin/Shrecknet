"""Novelist v5 orchestration.

Execution order: extract candidate evidence -> direct arcs -> load continuity -> plan arc writing
blocks -> write and locally check plain-prose sections -> render safe chapter HTML.
The analysis model receives the strict story-plan JSON Schema. The writer model
never receives a JSON schema because its contract is plain literary prose.
"""

from __future__ import annotations

import html
import json
import logging
import time
import traceback
from typing import Any, Awaitable, Callable

from jsonschema import validate as validate_json_schema

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.integrations.llm.structured_output import strict_json_schema
from app.jobs.novelist.block_planner import WritingBlock, arc_developments, chapter_word_target, plan_blocks
from app.jobs.novelist.debug_artifacts import NovelistDebugArtifacts
from app.jobs.novelist.context_budget import input_cost, require_context
from app.jobs.novelist.prompts import (
    build_analysis_retry_prompt,
    build_writer_prompt,
    build_writer_retry_prompt,
)
from app.jobs.novelist.prose_quality import prose_to_html, validate_prose_text, validate_contextual_prose
from app.jobs.novelist.narrative_director import direct_story
from app.jobs.novelist.source_interpreter import interpret_source
from app.jobs.novelist.story_plan import (
    STORY_PLAN_JSON_SCHEMA,
    INVENTORY_JSON_SCHEMA,
    SourceInventory,
    coverage_diagnostics,
    NarrativeStoryPlan,
    SourceSegment, CandidateExtraction, EvidenceInventory, DirectedStoryPlan,
    CANDIDATE_JSON_SCHEMA, EVIDENCE_INVENTORY_JSON_SCHEMA, DIRECTOR_SELECTION_JSON_SCHEMA, DirectedStorySelection,
)
from app.models.agent import Agent
from app.models.novelist import NovelistStage
from app.schemas.novelist import NovelistRunCreate

StageCallback = Callable[[NovelistStage, dict[str, Any]], Awaitable[None]]

logger = logging.getLogger(__name__)

ANALYSIS_MAX_TOKENS = 6000
WRITER_MAX_TOKENS = 5000


class NovelistOrchestrator:
    """Single authoritative implementation of the Novelist v5 pipeline."""

    def __init__(
        self, *, llm_client: ShreckLLMClient, model_policy: ModelPolicy,
        analysis_context_tokens: int = 64000, writer_context_tokens: int = 64000
    ) -> None:
        self.analysis_context_tokens = analysis_context_tokens
        self.writer_context_tokens = writer_context_tokens
        self.llm_client = llm_client
        self.analysis_model = getattr(
            model_policy, "model_novelist_analysis", None
        ) or model_policy.get_model(LLMTask.SYNTHESIS)
        self.writer_model = getattr(
            model_policy, "model_novelist_writer", None
        ) or model_policy.get_model(LLMTask.SYNTHESIS)
        self.calls: list[dict[str, Any]] = []
        self.debug = NovelistDebugArtifacts(None)
        self._active_block_id: str | None = None

    @staticmethod
    def _model_name(model: Any) -> Any:
        return model.model_dump() if hasattr(model, "model_dump") else str(model)

    async def _chat(self, **request: Any) -> Any:
        """Save the exact caller request before waiting, then the unmodified response."""
        tag = request["usage_tag"]
        snapshot = {**request, "model": self._model_name(request["model"])}
        self.debug.write(f"{tag}.request", request=snapshot)
        try:
            schema = (request.get("response_format") or {}).get("json_schema", {}).get("schema")
            context = self.analysis_context_tokens if schema is not None else self.writer_context_tokens
            require_context(request["messages"][0]["content"], schema,
                            context_tokens=context, output_tokens=request["max_tokens"])
            dispatched = time.monotonic()
            metrics = {
                "tag": tag, "role": "analysis" if schema is not None else "writer",
                "block_id": self._active_block_id if schema is None else None,
                "model": self._model_name(request["model"]),
                "prompt_bytes": len(request["messages"][0]["content"].encode("utf-8")),
                "schema_bytes": len(json.dumps(schema).encode("utf-8")) if schema is not None else 0,
                "input_cost_estimate": input_cost(request["messages"][0]["content"], schema),
                "is_retry": tag.endswith((".repair", "_retry")),
            }
            try:
                result = await self.llm_client.chat(**request)
            except Exception as exc:
                row = metrics | {"elapsed_ms": round((time.monotonic() - dispatched) * 1000, 2), "error": str(exc)}
                self.calls.append(row)
                self.debug.write("llm.metrics", **row)
                raise
            usage = result.get("usage") if isinstance(result, dict) else None
            usage = usage if isinstance(usage, dict) else {}
            metadata = result.get("response_metadata", {}) if isinstance(result, dict) else {}
            row = metrics | {
                "response_metadata": metadata, "provider_usage": usage,
                "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
                "elapsed_ms": round((time.monotonic() - dispatched) * 1000, 2),
            }
            self.calls.append(row)
            self.debug.write("llm.metrics", **row)
        except Exception as exc:
            self.debug.write(f"{tag}.error", error=str(exc), traceback=traceback.format_exc())
            raise
        self.debug.write(f"{tag}.response", response=result)
        return result

    async def _json(
        self, prompt: str, schema: dict[str, Any], usage_tag: str,
        *, validator: Callable[[dict[str, Any]], Any] | None = None
    ) -> dict[str, Any]:
        """Call the analysis role with its schema and one bounded repair attempt."""

        contracts = [
            (STORY_PLAN_JSON_SCHEMA, NarrativeStoryPlan, "novelist_v5_story_plan"),
            (INVENTORY_JSON_SCHEMA, SourceInventory, "novelist_v5_inventory"),
            (CANDIDATE_JSON_SCHEMA, CandidateExtraction, "novelist_v5_candidate"),
            (EVIDENCE_INVENTORY_JSON_SCHEMA, EvidenceInventory, "novelist_v5_evidence_inventory"),
            (DIRECTOR_SELECTION_JSON_SCHEMA, DirectedStorySelection, "novelist_v5_directed_selection"),
        ]
        contract = next((item for item in contracts if item[0] == schema), None)
        if contract is None:
            raise ValueError("Unknown Novelist structured schema")
        _, model_type, schema_name = contract
        response_format = strict_json_schema(schema_name, schema)
        rejected = ""
        last_error: Exception | None = None
        for attempt in range(2):
            attempt_tag = usage_tag if attempt == 0 else f"{usage_tag}.repair"
            attempt_prompt = prompt
            if attempt:
                attempt_prompt = build_analysis_retry_prompt(
                    original_prompt=prompt,
                    rejected=rejected,
                    error=last_error,
                    schema=schema,
                )
            self.debug.write("analysis.context", tag=attempt_tag, input_cost=input_cost(attempt_prompt, schema), context_tokens=self.analysis_context_tokens, output_tokens=ANALYSIS_MAX_TOKENS)
            result = await self._chat(
                model=self.analysis_model,
                messages=[{"role": "user", "content": attempt_prompt}],
                response_format=response_format,
                temperature=0.0,
                return_metadata=True,
                use_conversation_memory=False,
                usage_tag=attempt_tag,
                max_tokens=ANALYSIS_MAX_TOKENS,
            )
            text = result.get("text") if isinstance(result, dict) else result
            metadata = (
                result.get("response_metadata", {}) if isinstance(result, dict) else {}
            )
            rejected = str(text or "")
            try:
                finish_reason = str(metadata.get("finish_reason") or "").casefold()
                if finish_reason in {"length", "max_tokens", "max_output_tokens"}:
                    raise ValueError(
                        "analysis response was truncated at the output-token limit"
                    )
                parsed = json.loads(rejected)
                if not isinstance(parsed, dict):
                    raise ValueError("structured response must be a JSON object")
                validate_json_schema(parsed, schema)
                model_type.model_validate(parsed)
                if validator:
                    validator(parsed)
                self.debug.write(f"{attempt_tag}.validation", accepted=True, parsed=parsed)
                return parsed
            except Exception as exc:
                last_error = exc
                self.debug.write(f"{attempt_tag}.validation", accepted=False, error=str(exc))
                logger.warning(
                    "novelist_story_plan_invalid tag=%s attempt=%s max_tokens=%s error=%s",
                    usage_tag,
                    attempt + 1,
                    ANALYSIS_MAX_TOKENS,
                    exc,
                )
        raise RuntimeError(
            f"Novelist story-plan output failed after one retry: {last_error}"
        ) from last_error

    async def _write(
        self, prompt: str, *, usage_tag: str
    ) -> tuple[str, dict[str, Any]]:
        self.debug.write("writer.context", tag=usage_tag, input_cost=input_cost(prompt), context_tokens=self.writer_context_tokens, output_tokens=WRITER_MAX_TOKENS)
        result = await self._chat(
            model=self.writer_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            return_metadata=True,
            use_conversation_memory=False,
            usage_tag=usage_tag,
            max_tokens=WRITER_MAX_TOKENS,
        )
        text = result.get("text") if isinstance(result, dict) else result
        metadata = (
            result.get("response_metadata", {}) if isinstance(result, dict) else {}
        )
        return str(text or "").strip(), metadata

    @staticmethod
    def _source_for(
        block: WritingBlock, segments: list[SourceSegment]
    ) -> list[SourceSegment]:
        wanted = set(block.source_ids)
        return [segment for segment in segments if segment.id in wanted]

    async def _write_block(
        self,
        *,
        block: WritingBlock,
        plan: DirectedStoryPlan,
        segments: list[SourceSegment],
        continuity: dict[str, Any],
        payload: NovelistRunCreate,
        previous_tail: str,
        accepted_sections: list[str] | None = None,
    ) -> tuple[str, int]:
        self._active_block_id = block.block_id
        prompt = build_writer_prompt(
            plan=plan,
            block=block.as_prompt_dict(),
            source_segments=self._source_for(block, segments),
            language=payload.language or "",
            instructions=payload.instructions or "",
            continuity=continuity,
            previous_tail=previous_tail,
        )
        failures: list[str] = []
        prose = ""
        for attempt in range(2):
            request = (
                prompt
                if attempt == 0
                else build_writer_retry_prompt(
                    original_prompt=prompt, rejected=prose, errors=failures,
                )
            )
            prose, metadata = await self._write(
                request,
                usage_tag="novelist.writer.section"
                if attempt == 0
                else "novelist.writer.section_retry",
            )
            failures = validate_prose_text(
                prose,
                target_words=block.target_words,
                finish_reason=metadata.get("finish_reason"),
            )
            failures.extend(validate_contextual_prose(
                prose,
                forbidden_names=[member.player for member in plan.canonical_cast] + [item.player for item in plan.identity_restrictions],
                canonical_names=[member.character for member in plan.canonical_cast],
                accepted_sections=accepted_sections or [],
            ))
            self.debug.write(
                "writer.validation", block_id=block.block_id, attempt=attempt + 1,
                accepted=not failures, errors=failures,
            )
            if not failures:
                return prose, attempt + 1
        raise RuntimeError(
            f"Section {block.block_id} remained unusable after one retry: {'; '.join(failures)}"
        )

    async def execute(
        self,
        *,
        agent: Agent,
        payload: NovelistRunCreate,
        conversation_id: str | None = None,
        stage_callback: StageCallback | None = None,
    ) -> dict[str, Any]:
        self.calls = []
        self._active_block_id = None
        self.debug = NovelistDebugArtifacts.create(conversation_id)
        self.debug.write(
            "inputs", payload=payload.model_dump(),
            previous_run_context=getattr(payload, "previous_run_context", None),
            analysis_model=self._model_name(self.analysis_model),
            writer_model=self._model_name(self.writer_model),
        )
        stage = NovelistStage.INGEST
        self.debug.manifest(status="running", run_id=conversation_id, stage=stage.value)

        async def traced_stage(current: NovelistStage, data: dict[str, Any]) -> None:
            nonlocal stage
            stage = current
            self.debug.write(f"stage.{stage.value}", data=data)
            self.debug.manifest(status="running", run_id=conversation_id, stage=stage.value)
            if stage_callback:
                await stage_callback(current, data)

        try:
            result = await self._execute(
                agent=agent, payload=payload, conversation_id=conversation_id,
                stage_callback=traced_stage,
            )
        except Exception as exc:
            self.debug.write("failure", stage=stage.value, error=str(exc), traceback=traceback.format_exc())
            self.debug.manifest(status="failed", run_id=conversation_id, stage=stage.value, error=str(exc))
            raise
        self.debug.write("result", result=result)
        self.debug.manifest(status="completed", run_id=conversation_id, stage=NovelistStage.DONE.value)
        return result

    async def _execute(
        self,
        *,
        agent: Agent,
        payload: NovelistRunCreate,
        conversation_id: str | None = None,
        stage_callback: StageCallback | None = None,
    ) -> dict[str, Any]:
        del agent, conversation_id
        started = time.monotonic()
        artifacts: dict[str, Any] = {
            "pipeline_version": "v5",
            "pipeline_revision": "narrative-director-v2",
            "inputs": payload.model_dump(
                exclude={"previous_session_text", "previous_session_summary"}
            ),
            "timings_ms": {},
        }
        if stage_callback:
            await stage_callback(NovelistStage.INGEST, {"artifacts": artifacts})

        interpretation_started = time.monotonic()
        if stage_callback:
            await stage_callback(NovelistStage.INTERPRETATION, {"artifacts": artifacts})
        def record_planning(step: str, **values: Any) -> None:
            self.debug.write(step, **values)
            if step == "planning_path":
                artifacts.setdefault("planning", {})[step] = values

        candidate, segments = await interpret_source(
            text=payload.unstructured_text,
            source_type=payload.source_type,
            language=payload.language or "",
            instructions=payload.instructions or "",
            call_json=self._json,
            context_tokens=self.analysis_context_tokens,
            output_tokens=ANALYSIS_MAX_TOKENS,
            pages=getattr(payload, "source_pages", None),
            diagnostic=record_planning,
        )
        artifacts["candidate_extraction"] = candidate.model_dump()
        artifacts["story_plan"] = candidate.legacy_plan().model_dump()
        artifacts["source_index_version"] = "paragraph-v1"
        artifacts["coverage"] = coverage_diagnostics(candidate, segments)
        artifacts["source_segments"] = [segment.model_dump() for segment in segments]
        artifacts["timings_ms"]["interpretation"] = round(
            (time.monotonic() - interpretation_started) * 1000, 2
        )

        director_started = time.monotonic()
        if stage_callback:
            await stage_callback(NovelistStage.INTERPRETATION, {"artifacts": artifacts, "analysis_step": "narrative_director"})
        plan = await direct_story(
            candidate=candidate, instructions=payload.instructions or "",
            language=payload.language or "", call_json=self._json,
        )
        artifacts["directed_story_plan"] = plan.model_dump()
        artifacts["timings_ms"]["director"] = round((time.monotonic() - director_started) * 1000, 2)
        artifacts["coverage"]["arc_beats"] = {arc.arc_id: arc.beat_ids for arc in plan.arcs}
        artifacts["coverage"]["excluded_beat_ids"] = plan.excluded_beat_ids

        if stage_callback:
            await stage_callback(NovelistStage.CONTINUITY, {"artifacts": artifacts})
        continuity = {
            "previous_session": payload.previous_session_summary
            or payload.previous_session_text
            or "",
            "previous_run_context": getattr(payload, "previous_run_context", None),
            "authority_order": [
                "current_story_plan_and_source",
                "previous_session",
                "previous_run_context",
                "writing_style",
            ],
        }
        artifacts["continuity"] = continuity

        # Full history stays in artifacts; the writer receives only matching,
        # bounded prior sentences and no historical source reference IDs.
        names = [member.character.casefold() for member in plan.canonical_cast]
        prior = continuity["previous_session"]
        previous = continuity.get("previous_run_context") or {}
        if isinstance(previous, dict):
            historical = previous.get("directed_story_plan") or previous.get("story_plan") or previous.get("evidence_ledger") or {}
            def narrative_strings(value: Any, key: str = "") -> list[str]:
                if isinstance(value, str):
                    return [value] if key in {"summary", "outline", "description", "statement", "text", "fact"} else []
                if isinstance(value, list):
                    return [text for item in value for text in narrative_strings(item, key)]
                if isinstance(value, dict):
                    return [text for field, item in value.items() for text in narrative_strings(item, field)]
                return []
            prior += "\n" + "\n".join(narrative_strings(historical))
        relevant = [sentence.strip() for sentence in prior.splitlines() if not names or any(name in sentence.casefold() for name in names)]
        compact_continuity = {"prior_context": "\n".join(relevant)[:1000], "authority": "lower_than_current_source"}
        empty_block = {"block_id": "block-000", "arc_id": "arc-000", "beat_ids": [],
                       "target_words": 1400, "is_final": False, "part_index": 999, "part_count": 999,
                       "developments": []}
        # Reserve each arc's own details plus the maximum UTF-8 continuity tail.
        base_prompts = {arc.arc_id: build_writer_prompt(
            plan=plan, block=empty_block | {"beat_ids": arc.beat_ids, "developments": arc_developments(candidate, arc.beat_ids)},
            source_segments=[], language=payload.language or "", instructions=payload.instructions or "",
            continuity=compact_continuity, previous_tail="𐍈" * 700,
        ) for arc in plan.arcs}
        available = {arc_id: int(self.writer_context_tokens * 0.8) - WRITER_MAX_TOKENS - input_cost(prompt) - 8192
                     for arc_id, prompt in base_prompts.items()}
        artifacts["chapter_target_words"] = chapter_word_target(segments)
        blocks = plan_blocks(plan, candidate, segments, max_source_bytes=available)
        artifacts["block_plan"] = [
            block.as_prompt_dict() | {"source_ids": block.source_ids}
            for block in blocks
        ]
        if stage_callback:
            await stage_callback(
                NovelistStage.BLOCK_PLANNING,
                {"artifacts": artifacts, "block_count": len(blocks)},
            )

        accepted: dict[str, str] = {}
        attempts: dict[str, int] = {}
        previous_tail = ""
        writing_started = time.monotonic()
        for index, block in enumerate(blocks, start=1):
            if stage_callback:
                await stage_callback(
                    NovelistStage.WRITING,
                    {
                        "artifacts": artifacts,
                        "block_count": len(blocks),
                        "completed_blocks": index - 1,
                    },
                )
            prose, used = await self._write_block(
                block=block,
                plan=plan,
                segments=segments,
                continuity=compact_continuity,
                payload=payload,
                previous_tail=previous_tail,
                accepted_sections=list(accepted.values()),
            )
            accepted[block.block_id] = prose
            attempts[block.block_id] = used
            previous_tail = prose[-700:]

        if stage_callback:
            await stage_callback(
                NovelistStage.QUALITY_GATE,
                {
                    "artifacts": artifacts,
                    "block_count": len(blocks),
                    "completed_blocks": len(blocks),
                },
            )
        artifacts["timings_ms"]["writing"] = round((time.monotonic() - writing_started) * 1000, 2)
        merging_started = time.monotonic()
        title = (plan.title or "Chapter").strip() or "Chapter"
        rendered_sections = [
            prose_to_html(accepted[block.block_id]) for block in blocks
        ]
        final_html = f"<h1>{html.escape(title, quote=False)}</h1>\n" + "\n".join(
            rendered_sections
        )
        if stage_callback:
            await stage_callback(
                NovelistStage.MERGING,
                {"artifacts": artifacts, "draft_text": final_html},
            )

        artifacts["blocks"] = [
            {
                "block_id": block.block_id,
                "arc_id": block.arc_id,
                "beat_ids": block.beat_ids,
                "source_ids": block.source_ids,
                "prose": accepted[block.block_id],
                "attempts": attempts[block.block_id],
            }
            for block in blocks
        ]
        artifacts["quality_gate"] = {
            "all_blocks_accepted": True,
            "total_retries": sum(value - 1 for value in attempts.values()),
        }
        artifacts["timings_ms"]["merging"] = round((time.monotonic() - merging_started) * 1000, 2)
        artifacts["timings_ms"]["total"] = round((time.monotonic() - started) * 1000, 2)
        artifacts["llm_calls"] = self.calls
        artifacts["efficiency"] = {
            "writing_calls": sum(call["role"] == "writer" for call in self.calls),
            "total_calls": len(self.calls),
            "retry_count": sum(call.get("is_retry", False) for call in self.calls),
            "writer_prompt_bytes": sum(call["prompt_bytes"] for call in self.calls if call["role"] == "writer"),
            "reported_input_tokens": sum(call["input_tokens"] for call in self.calls if isinstance(call.get("input_tokens"), int)),
            "reported_output_tokens": sum(call["output_tokens"] for call in self.calls if isinstance(call.get("output_tokens"), int)),
            "calls_missing_usage": sum(not isinstance(call.get("input_tokens"), int) or not isinstance(call.get("output_tokens"), int) for call in self.calls),
        }
        artifacts["output_summary"] = {
            "block_count": len(blocks),
            "fidelity_status": "not_run",
            "correction_count": 0,
        }
        return {
            "final_text_html": final_html,
            "artifacts": artifacts,
            "fidelity_status": "not_run",
            "correction_count": 0,
        }
