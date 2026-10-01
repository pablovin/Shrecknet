"""Novelist v3 orchestration.

Execution order: interpret source -> enrich continuity -> deterministically plan
blocks -> write and structurally validate each block -> merge -> verify factual
fidelity -> correct only named blocks once.  The analysis role frames and
verifies; the writer role only renders ledger-backed prose.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Awaitable, Callable

from jsonschema import validate as validate_json_schema

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.integrations.llm.structured_output import chat_with_structured_output, strict_json_schema
from app.jobs.shrecknet.agent import parse_json_deterministically
from app.jobs.novelist.block_planner import WritingBlock, plan_blocks
from app.jobs.novelist.evidence_ledger import LedgerScene, NarrativeEvidenceLedger
from app.jobs.novelist.prose_quality import validate_prose_html
from app.jobs.novelist.source_interpreter import interpret_source
from app.models.agent import Agent
from app.models.novelist import NovelistStage
from app.schemas.novelist import NovelistRunCreate

StageCallback = Callable[[NovelistStage, dict[str, Any]], Awaitable[None]]

logger = logging.getLogger(__name__)

_VERIFY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["issues"], "properties": {"issues": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["block_id", "kind", "detail"], "properties": {"block_id": {"type": "string"}, "kind": {"type": "string"}, "detail": {"type": "string"}}}}}}


class NovelistOrchestrator:
    """Single authoritative implementation of the Novelist v3 pipeline."""

    def __init__(self, *, llm_client: ShreckLLMClient, model_policy: ModelPolicy) -> None:
        self.llm_client = llm_client
        self.analysis_model = getattr(model_policy, "model_novelist_analysis", None) or model_policy.get_model(LLMTask.SYNTHESIS)
        self.writer_model = getattr(model_policy, "model_novelist_writer", None) or model_policy.get_model(LLMTask.SYNTHESIS)
        self.calls: list[dict[str, Any]] = []

    async def _json(self, prompt: str, schema: dict[str, Any], usage_tag: str) -> dict[str, Any]:
        response_format = strict_json_schema("novelist_v3", schema)
        last: Exception | None = None
        for attempt in range(2):
            try:
                if attempt == 0:
                    result = await chat_with_structured_output(
                        llm_client=self.llm_client,
                        model=self.analysis_model,
                        messages=[{"role": "system", "content": prompt}],
                        response_format=response_format,
                        temperature=0.0,
                        return_metadata=True,
                        usage_tag=usage_tag,
                        max_tokens=6000,
                    )
                else:
                    # Some OpenAI-compatible providers acknowledge json_schema
                    # but return a short non-JSON response.  Retrying the same
                    # native request repeats that provider failure, so make the
                    # compatibility path explicit while retaining the complete
                    # source-bearing prompt and validating locally.
                    fallback_prompt = (
                        f"{prompt}\n\nNative structured output was not valid. "
                        "Return one RFC8259 JSON object only: no Markdown, explanation, "
                        f"or omitted required fields. Required JSON Schema: {json.dumps(schema, ensure_ascii=False)}"
                    )
                    result = await self.llm_client.chat(
                        model=self.analysis_model,
                        messages=[{"role": "system", "content": fallback_prompt}],
                        temperature=0.0,
                        return_metadata=True,
                        usage_tag=f"{usage_tag}.malformed_structured_fallback",
                        max_tokens=6000,
                    )
                text = result.get("text") if isinstance(result, dict) else result
                parsed = parse_json_deterministically(str(text))
                if not isinstance(parsed, dict):
                    raise ValueError("structured response must be a JSON object")
                validate_json_schema(parsed, schema)
                self.calls.append({"tag": usage_tag, "model": self.analysis_model.model_dump() if hasattr(self.analysis_model, "model_dump") else str(self.analysis_model)})
                return parsed
            except Exception as exc:
                last = exc
                logger.warning(
                    "novelist_analysis_structured_output_invalid tag=%s attempt=%s error=%s",
                    usage_tag,
                    attempt + 1,
                    exc,
                )
        raise RuntimeError(f"Novelist analysis structured output failed after retry: {last}") from last

    async def _write(self, prompt: str, *, usage_tag: str) -> str:
        result = await self.llm_client.chat(model=self.writer_model, messages=[{"role": "system", "content": prompt}], temperature=0.7, return_metadata=True, use_conversation_memory=False, usage_tag=usage_tag, max_tokens=3000)
        text = result.get("text") if isinstance(result, dict) else result
        self.calls.append({"tag": usage_tag, "model": self.writer_model.model_dump() if hasattr(self.writer_model, "model_dump") else str(self.writer_model)})
        return str(text or "").strip()

    @staticmethod
    def _scenes_for(block: WritingBlock, ledger: NarrativeEvidenceLedger) -> list[LedgerScene]:
        wanted = set(block.scene_ids)
        return [scene for scene in sorted(ledger.scenes, key=lambda item: (item.chronology, item.scene_id)) if scene.scene_id in wanted]

    async def _write_block(self, *, block: WritingBlock, ledger: NarrativeEvidenceLedger, continuity: dict[str, Any], payload: NovelistRunCreate, previous_tail: str, correction: list[dict[str, str]] | None = None) -> tuple[str, int]:
        scenes = [scene.model_dump() for scene in self._scenes_for(block, ledger)]
        prompt = f"""Stage 4 — Progressive Narrative Writing. Write only HTML <p> and optional <blockquote> elements for {block.block_id}. The ledger defines WHAT happened; you define only HOW it is narrated.

Never add events, clues, characters, injuries, relationships, destinations, motives, factual knowledge, changed outcomes, or chronology. Reconstructed dialogue is allowed only when its meaning is ledger-supported. Do not write lists, headings, pseudo-lists, or a rushed ending. Produce continuous literary prose in {payload.language or 'the source language'}, about {block.target_words} words.

user_instructions={payload.instructions or 'none'}
complete_cast_mapping={json.dumps(ledger.player_character_mapping, ensure_ascii=False)}
chapter_outline={json.dumps([{ 'scene_id': s.scene_id, 'title': s.title, 'chronology': s.chronology } for s in ledger.scenes], ensure_ascii=False)}
continuity_package={json.dumps(continuity, ensure_ascii=False)}
current_block_evidence={json.dumps(scenes, ensure_ascii=False)}
previous_accepted_tail={previous_tail}
correction_issues={json.dumps(correction or [], ensure_ascii=False)}"""
        for attempt in range(2):
            html = await self._write(prompt + ("" if attempt == 0 else "\nRepair these deterministic structural violations exactly; preserve the same evidence."), usage_tag="novelist.writer.block" if not correction else "novelist.writer.correction")
            violations = validate_prose_html(html)
            if not violations:
                return html, attempt + 1
            prompt += "\nStructural violations from the previous attempt: " + "; ".join(violations)
        raise RuntimeError(f"Block {block.block_id} failed prose quality gate: {violations}")

    async def _verify(self, *, html: str, ledger: NarrativeEvidenceLedger, blocks: list[WritingBlock]) -> list[dict[str, str]]:
        prompt = f"""Stage 5 — Fidelity Verification. Compare final chapter HTML with the Narrative Evidence Ledger. Detect unsupported events/dialogue/motives, wrong actor or speaker, chronology errors, OOC leakage, continuity conflicts, and missing major events. Return JSON exactly {{"issues":[{{"block_id":"block-001", "kind":"...", "detail":"..."}}]}}. Only name block IDs from {json.dumps([block.block_id for block in blocks])}. Do not criticize literary quality.\nledger={ledger.model_dump_json()}\nchapter_html={html}"""
        parsed = await self._json(prompt, _VERIFY_SCHEMA, "novelist.analysis.fidelity")
        return [issue for issue in parsed.get("issues", []) if isinstance(issue, dict)]

    async def execute(self, *, agent: Agent, payload: NovelistRunCreate, conversation_id: str | None = None, stage_callback: StageCallback | None = None) -> dict[str, Any]:
        del agent, conversation_id
        started = time.monotonic()
        artifacts: dict[str, Any] = {"pipeline_version": "v3", "inputs": payload.model_dump(exclude={"previous_session_text", "previous_session_summary"}), "timings_ms": {}}
        if stage_callback: await stage_callback(NovelistStage.INGEST, {"artifacts": artifacts})
        t0 = time.monotonic()
        if stage_callback: await stage_callback(NovelistStage.INTERPRETATION, {"artifacts": artifacts})
        ledger, segments = await interpret_source(text=payload.unstructured_text.strip(), source_type=payload.source_type, language=payload.language or "", instructions=payload.instructions or "", call_json=self._json)
        artifacts["evidence_ledger"] = ledger.model_dump(); artifacts["source_segments"] = [segment.model_dump() for segment in segments]; artifacts["timings_ms"]["interpretation"] = round((time.monotonic()-t0)*1000, 2)
        if stage_callback: await stage_callback(NovelistStage.CONTINUITY, {"artifacts": artifacts})
        continuity = {"previous_session": payload.previous_session_summary or payload.previous_session_text or "", "prior_ledger": getattr(payload, "previous_ledger", None), "graph_context": [], "character_guidance": [], "authority_order": ["current_evidence_ledger", "previous_session", "graph_world", "character_agent", "writing_style"]}
        artifacts["continuity"] = continuity
        blocks = plan_blocks(ledger.scenes)
        artifacts["block_plan"] = [{"block_id": block.block_id, "scene_ids": block.scene_ids, "target_words": block.target_words} for block in blocks]
        if stage_callback: await stage_callback(NovelistStage.BLOCK_PLANNING, {"artifacts": artifacts, "block_count": len(blocks)})
        accepted: dict[str, str] = {}; attempts: dict[str, int] = {}; previous_tail = ""
        for index, block in enumerate(blocks, start=1):
            if stage_callback: await stage_callback(NovelistStage.WRITING, {"artifacts": artifacts, "block_count": len(blocks), "completed_blocks": index - 1})
            html, used = await self._write_block(block=block, ledger=ledger, continuity=continuity, payload=payload, previous_tail=previous_tail)
            accepted[block.block_id], attempts[block.block_id] = html, used
            previous_tail = html[-1800:]
        if stage_callback: await stage_callback(NovelistStage.QUALITY_GATE, {"artifacts": artifacts, "block_count": len(blocks), "completed_blocks": len(blocks)})
        title = ledger.chapter_title.strip() if ledger.chapter_title else "Chapter"
        final_html = f"<h1>{title}</h1>\n" + "\n".join(accepted[block.block_id] for block in blocks)
        if stage_callback: await stage_callback(NovelistStage.MERGING, {"artifacts": artifacts, "draft_text": final_html})
        if stage_callback: await stage_callback(NovelistStage.FIDELITY, {"artifacts": artifacts})
        issues = await self._verify(html=final_html, ledger=ledger, blocks=blocks)
        corrections = 0
        if issues:
            corrections = 1
            if stage_callback: await stage_callback(NovelistStage.CORRECTION, {"artifacts": artifacts, "affected_blocks": sorted({row['block_id'] for row in issues})})
            for block in blocks:
                block_issues = [row for row in issues if row.get("block_id") == block.block_id]
                if block_issues:
                    accepted[block.block_id], attempts[block.block_id] = await self._write_block(block=block, ledger=ledger, continuity=continuity, payload=payload, previous_tail="", correction=block_issues)
            final_html = f"<h1>{title}</h1>\n" + "\n".join(accepted[block.block_id] for block in blocks)
            remaining = await self._verify(html=final_html, ledger=ledger, blocks=blocks)
            if remaining:
                raise RuntimeError("Novelist fidelity verification failed after targeted correction")
        artifacts["blocks"] = [{"block_id": block.block_id, "scene_ids": block.scene_ids, "html": accepted[block.block_id], "attempts": attempts[block.block_id]} for block in blocks]
        artifacts["quality_gate"] = {"all_blocks_accepted": True}; artifacts["fidelity"] = {"status": "passed", "initial_issues": issues}; artifacts["timings_ms"]["total"] = round((time.monotonic()-started)*1000, 2); artifacts["llm_calls"] = self.calls
        artifacts["output_summary"] = {"block_count": len(blocks), "fidelity_status": "passed", "correction_count": corrections}
        return {"final_text_html": final_html, "artifacts": artifacts, "fidelity_status": "passed", "correction_count": corrections}
