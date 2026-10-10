"""Novelist v4 orchestration.

Execution order: understand source -> load continuity -> plan adjacent writing
blocks -> write and locally check plain-prose sections -> render safe chapter HTML.
The analysis model receives the strict story-plan JSON Schema. The writer model
never receives a JSON schema because its contract is plain literary prose.
"""

from __future__ import annotations

import html
import json
import logging
import time
from typing import Any, Awaitable, Callable

from jsonschema import validate as validate_json_schema

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.integrations.llm.structured_output import strict_json_schema
from app.jobs.novelist.block_planner import WritingBlock, plan_blocks
from app.jobs.novelist.prompts import (
    build_analysis_retry_prompt,
    build_writer_prompt,
    build_writer_retry_prompt,
)
from app.jobs.novelist.prose_quality import prose_to_html, validate_prose_text
from app.jobs.novelist.source_interpreter import interpret_source
from app.jobs.novelist.story_plan import (
    STORY_PLAN_JSON_SCHEMA,
    NarrativeStoryPlan,
    SourceSegment,
)
from app.models.agent import Agent
from app.models.novelist import NovelistStage
from app.schemas.novelist import NovelistRunCreate

StageCallback = Callable[[NovelistStage, dict[str, Any]], Awaitable[None]]

logger = logging.getLogger(__name__)

ANALYSIS_MAX_TOKENS = 3000
WRITER_MAX_TOKENS = 5000


class NovelistOrchestrator:
    """Single authoritative implementation of the Novelist v4 pipeline."""

    def __init__(
        self, *, llm_client: ShreckLLMClient, model_policy: ModelPolicy
    ) -> None:
        self.llm_client = llm_client
        self.analysis_model = getattr(
            model_policy, "model_novelist_analysis", None
        ) or model_policy.get_model(LLMTask.SYNTHESIS)
        self.writer_model = getattr(
            model_policy, "model_novelist_writer", None
        ) or model_policy.get_model(LLMTask.SYNTHESIS)
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def _model_name(model: Any) -> Any:
        return model.model_dump() if hasattr(model, "model_dump") else str(model)

    async def _json(
        self, prompt: str, schema: dict[str, Any], usage_tag: str
    ) -> dict[str, Any]:
        """Call the analysis role with its schema and one bounded repair attempt."""

        if schema != STORY_PLAN_JSON_SCHEMA:
            raise ValueError("Novelist analysis calls must use STORY_PLAN_JSON_SCHEMA")
        response_format = strict_json_schema("novelist_v4_story_plan", schema)
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
            result = await self.llm_client.chat(
                model=self.analysis_model,
                messages=[{"role": "user", "content": attempt_prompt}],
                response_format=response_format,
                temperature=0.0,
                return_metadata=True,
                usage_tag=attempt_tag,
                max_tokens=ANALYSIS_MAX_TOKENS,
            )
            text = result.get("text") if isinstance(result, dict) else result
            metadata = (
                result.get("response_metadata", {}) if isinstance(result, dict) else {}
            )
            rejected = str(text or "")
            self.calls.append(
                {
                    "tag": attempt_tag,
                    "role": "analysis",
                    "model": self._model_name(self.analysis_model),
                    "response_metadata": metadata,
                }
            )
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
                return parsed
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "novelist_story_plan_invalid tag=%s attempt=%s error=%s",
                    usage_tag,
                    attempt + 1,
                    exc,
                )
        raise RuntimeError(
            f"Novelist story-plan output failed after one retry: {last_error}"
        ) from last_error

    async def _write(
        self, prompt: str, *, usage_tag: str
    ) -> tuple[str, dict[str, Any]]:
        result = await self.llm_client.chat(
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
        self.calls.append(
            {
                "tag": usage_tag,
                "role": "writer",
                "model": self._model_name(self.writer_model),
                "response_metadata": metadata,
            }
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
        plan: NarrativeStoryPlan,
        segments: list[SourceSegment],
        continuity: dict[str, Any],
        payload: NovelistRunCreate,
        previous_tail: str,
    ) -> tuple[str, int]:
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
        del agent, conversation_id
        started = time.monotonic()
        artifacts: dict[str, Any] = {
            "pipeline_version": "v4",
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
        plan, segments = await interpret_source(
            text=payload.unstructured_text.strip(),
            source_type=payload.source_type,
            language=payload.language or "",
            instructions=payload.instructions or "",
            call_json=self._json,
        )
        artifacts["story_plan"] = plan.model_dump()
        artifacts["source_segments"] = [segment.model_dump() for segment in segments]
        artifacts["timings_ms"]["interpretation"] = round(
            (time.monotonic() - interpretation_started) * 1000, 2
        )

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

        blocks = plan_blocks(plan.beats)
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
                continuity=continuity,
                payload=payload,
                previous_tail=previous_tail,
            )
            accepted[block.block_id] = prose
            attempts[block.block_id] = used
            previous_tail = prose[-1800:]

        if stage_callback:
            await stage_callback(
                NovelistStage.QUALITY_GATE,
                {
                    "artifacts": artifacts,
                    "block_count": len(blocks),
                    "completed_blocks": len(blocks),
                },
            )
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
        artifacts["timings_ms"]["total"] = round((time.monotonic() - started) * 1000, 2)
        artifacts["llm_calls"] = self.calls
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
