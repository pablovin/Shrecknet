"""Simplified Novelist orchestrator with strict structure controls."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Awaitable, Callable

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.jobs.novelist.prompts import (
    CONTINUITY_BRIEF_PROMPT,
    CRITIC_PROMPT,
    ELDER_QUERY_PLANNING_PROMPT,
    EVENT_COVERAGE_PROMPT,
    PART_PROMPT,
    PLAN_PROMPT,
    REVISION_PROMPT,
)
from app.models.agent import Agent
from app.models.novelist import NovelistStage
from app.schemas.novelist import NovelistRunCreate

logger = logging.getLogger(__name__)

StageCallback = Callable[[NovelistStage, dict[str, Any]], Awaitable[None]]
ElderQueryRunner = Callable[[Agent, str], Awaitable[list[dict[str, Any]]]]


class NovelistOrchestrator:
    """Chapter pipeline: plan -> elder enrich (optional) -> write -> critic -> revise -> merge."""

    def __init__(
        self,
        *,
        llm_client: OpenAIClient,
        model_policy: ModelPolicy,
        max_concurrency: int = 4,
        elder_query_runner: ElderQueryRunner | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.model_policy = model_policy
        self.draft_model = getattr(model_policy, "model_novelist_draft", None)
        self.critic_model = getattr(model_policy, "model_novelist_critic", None)
        self.max_concurrency = max_concurrency
        self.elder_query_runner = elder_query_runner

    async def execute(
        self,
        *,
        agent: Agent,
        payload: NovelistRunCreate,
        conversation_id: str | None = None,
        stage_callback: StageCallback | None = None,
    ) -> dict[str, Any]:
        unstructured_text = payload.unstructured_text.strip()
        language = (payload.language or "").strip()
        instructions = (payload.instructions or "").strip()
        previous_session_text = (payload.previous_session_text or "").strip()
        continuity_brief = (payload.previous_session_summary or "").strip()
        if not continuity_brief:
            continuity_brief = await self._build_continuity_brief(
                previous_session_text=previous_session_text,
                language=language,
                instructions=instructions,
                conversation_id=conversation_id,
            )

        # Stage 1: Planning
        plan_prompt = self._build_plan_user_prompt(
            unstructured_text=unstructured_text,
            language=language,
            instructions=instructions,
            agent=agent,
            continuity_brief=continuity_brief,
        )
        plan_system_prompt = self._compose_system_prompt(
            PLAN_PROMPT, language=language, instructions=instructions
        )
        raw_plan = await self.llm_client.chat(
            model=self.model_policy.get_model(LLMTask.VALIDATION),
            messages=[
                {"role": "system", "content": plan_system_prompt},
                {"role": "user", "content": plan_prompt},
            ],
            temperature=0.3,
            conversation_id=conversation_id,
        )
        parsed_plan = self._parse_plan(raw_plan)

        # Stage 1.5: Plan Elder queries from assigned events and fetch flavor-only context.
        elder_query_plan_system_prompt = self._compose_system_prompt(
            ELDER_QUERY_PLANNING_PROMPT,
            language=language,
            instructions=instructions,
        )
        elder_query_plan_user_prompt = self._build_elder_query_plan_user_prompt(
            parsed_plan=parsed_plan,
            source_context=self._short_source_context(unstructured_text),
            continuity_brief=continuity_brief,
            language=language,
            instructions=instructions,
            agent=agent,
        )
        raw_elder_query_plan = ""
        try:
            raw_elder_query_plan = await self.llm_client.chat(
                model=self.critic_model or self.model_policy.get_model(LLMTask.VALIDATION),
                messages=[
                    {"role": "system", "content": elder_query_plan_system_prompt},
                    {"role": "user", "content": elder_query_plan_user_prompt},
                ],
                temperature=0.2,
                conversation_id=conversation_id,
            )
        except Exception:
            logger.warning(
                "Failed Elder query planning; using deterministic fallback",
                exc_info=True,
            )
        elder_query_plan = self._parse_elder_query_plan(
            raw=raw_elder_query_plan,
            parsed_plan=parsed_plan,
        )
        # Elder context is flavor-only support; it must never change assigned events.
        elder_context_by_part = await self._collect_elder_context_by_part(
            agent=agent,
            elder_query_plan=elder_query_plan,
        )
        if stage_callback:
            await stage_callback(
                NovelistStage.PLANNING,
                {
                    "plan_raw": raw_plan,
                    "plan_parsed": parsed_plan,
                    "elder_query_plan_raw": raw_elder_query_plan,
                    "elder_query_plan_parsed": elder_query_plan,
                    "elder_context_by_part": elder_context_by_part,
                },
            )

        # Stage 2: Write parts sequentially and carry short summaries forward.
        write_part_prompts: dict[str, dict[str, str]] = {}
        part_keys = ("part_1", "part_2", "part_3")
        draft_parts: dict[str, str] = {}
        part_summaries: dict[str, str] = {}
        short_source_context = self._short_source_context(unstructured_text)
        part_system_prompt = self._compose_system_prompt(
            PART_PROMPT, language=language, instructions=instructions
        )

        for part_key in part_keys:
            part_data = parsed_plan[part_key]
            user_prompt = self._build_part_user_prompt(
                part_key=part_key,
                part_data=part_data,
                source_context=short_source_context,
                language=language,
                instructions=instructions,
                agent=agent,
                previous_summaries=[part_summaries[k] for k in part_keys if k in part_summaries],
                continuity_brief=continuity_brief,
                elder_context_lines=self._normalize_text_list(
                    elder_context_by_part.get(part_key, {}).get("elder_context", []),
                    max_items=8,
                ),
            )
            write_part_prompts[part_key] = {
                "system_prompt": part_system_prompt,
                "user_prompt": user_prompt,
            }
            raw_part = await self.llm_client.chat(
                model=self.draft_model or self.model_policy.get_model(LLMTask.SYNTHESIS),
                messages=[
                    {"role": "system", "content": part_system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,  # Lower creativity to prioritize correctness.
                conversation_id=conversation_id,
            )
            cleaned_part = self._ensure_readable_html(raw_part)
            draft_parts[part_key] = cleaned_part
            part_summary = self._summarize_part_for_context(
                part_key=part_key,
                part_title=part_data.get("title", part_key),
                part_html=cleaned_part,
            )
            part_summaries[part_key] = part_summary
        if stage_callback:
            await stage_callback(
                NovelistStage.WRITING,
                {"parts": draft_parts, "part_summaries": part_summaries},
            )

        # Stage 3: Critic on merged draft
        pre_critic_draft = self._merge_parts_html(parsed_plan, draft_parts)
        critic_user_prompt = self._build_critic_user_prompt(
            parsed_plan=parsed_plan,
            draft_text=pre_critic_draft,
            language=language,
            instructions=instructions,
        )
        critic_system_prompt = self._compose_system_prompt(
            CRITIC_PROMPT, language=language, instructions=instructions
        )
        raw_critic = await self.llm_client.chat(
            model=self.critic_model or self.model_policy.get_model(LLMTask.VALIDATION),
            messages=[
                {"role": "system", "content": critic_system_prompt},
                {"role": "user", "content": critic_user_prompt},
            ],
            temperature=0.3,
            conversation_id=conversation_id,
        )
        critic_notes = self._parse_critic(raw_critic)
        if stage_callback:
            await stage_callback(
                NovelistStage.CRITIC,
                {"draft_text": pre_critic_draft, "critic_notes": critic_notes},
            )

        # Stage 4: Apply critic to each part with strict revision constraints.
        revise_part_prompts: dict[str, dict[str, str]] = {}
        coverage_part_prompts: dict[str, dict[str, str]] = {}
        coverage_reports: dict[str, dict[str, Any]] = {}
        revised_parts: dict[str, str] = {}
        revise_system_prompt = self._compose_system_prompt(
            REVISION_PROMPT, language=language, instructions=instructions
        )
        coverage_system_prompt = self._compose_system_prompt(
            EVENT_COVERAGE_PROMPT, language=language, instructions=instructions
        )
        for part_key in part_keys:
            part_data = parsed_plan[part_key]
            part_text = draft_parts.get(part_key, "")
            part_note = critic_notes.get(f"{part_key}_notes", "")
            remove_items = critic_notes.get(f"remove_from_{part_key}", [])
            user_prompt = self._build_revise_user_prompt(
                part_key=part_key,
                part_data=part_data,
                part_text=part_text,
                part_critic_note=part_note,
                global_critic_note=critic_notes.get("global_notes", ""),
                remove_items=remove_items if isinstance(remove_items, list) else [],
                source_context=short_source_context,
                language=language,
                instructions=instructions,
                agent=agent,
                continuity_brief=continuity_brief,
                elder_context_lines=self._normalize_text_list(
                    elder_context_by_part.get(part_key, {}).get("elder_context", []),
                    max_items=8,
                ),
            )
            revise_part_prompts[part_key] = {
                "system_prompt": revise_system_prompt,
                "user_prompt": user_prompt,
            }
            revised_raw = await self.llm_client.chat(
                model=self.draft_model or self.model_policy.get_model(LLMTask.SYNTHESIS),
                messages=[
                    {"role": "system", "content": revise_system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,  # Lower creativity to prioritize correctness.
                conversation_id=conversation_id,
            )
            revised_html = self._ensure_readable_html(revised_raw)
            coverage_user_prompt = self._build_event_coverage_user_prompt(
                part_key=part_key,
                part_title=part_data.get("title", part_key),
                part_events=part_data.get("events", []),
                part_html=revised_html,
                language=language,
                instructions=instructions,
            )
            coverage_part_prompts[part_key] = {
                "system_prompt": coverage_system_prompt,
                "user_prompt": coverage_user_prompt,
            }
            coverage_raw = await self.llm_client.chat(
                model=self.critic_model or self.model_policy.get_model(LLMTask.VALIDATION),
                messages=[
                    {"role": "system", "content": coverage_system_prompt},
                    {"role": "user", "content": coverage_user_prompt},
                ],
                temperature=0.1,
                conversation_id=conversation_id,
            )
            coverage_result = self._parse_event_coverage(
                coverage_raw=coverage_raw,
                fallback_html=revised_html,
            )
            # Fail-safe retry if validator still reports missing assigned events.
            missing_after = coverage_result.get("missing_after", [])
            if isinstance(missing_after, list) and missing_after:
                retry_prompt = self._build_event_coverage_user_prompt(
                    part_key=part_key,
                    part_title=part_data.get("title", part_key),
                    part_events=part_data.get("events", []),
                    part_html=str(coverage_result.get("revised_html", revised_html)),
                    language=language,
                    instructions=instructions,
                    required_missing=[str(item) for item in missing_after if str(item).strip()],
                )
                retry_raw = await self.llm_client.chat(
                    model=self.critic_model or self.model_policy.get_model(LLMTask.VALIDATION),
                    messages=[
                        {"role": "system", "content": coverage_system_prompt},
                        {"role": "user", "content": retry_prompt},
                    ],
                    temperature=0.1,
                    conversation_id=conversation_id,
                )
                coverage_result = self._parse_event_coverage(
                    coverage_raw=retry_raw,
                    fallback_html=str(coverage_result.get("revised_html", revised_html)),
                )
            coverage_reports[part_key] = coverage_result
            revised_parts[part_key] = self._ensure_readable_html(
                coverage_result.get("revised_html", revised_html)
            )
        if stage_callback:
            await stage_callback(
                NovelistStage.APPLY_CRITIC,
                {
                    "critic_notes": critic_notes,
                    "parts_revised": revised_parts,
                    "coverage_reports": coverage_reports,
                },
            )

        # Stage 5: Final merge
        if stage_callback:
            await stage_callback(NovelistStage.MERGING, {})
        final_text = self._merge_parts_html(parsed_plan, revised_parts)

        artifacts = {
            "inputs": {
                "unstructured_text": payload.unstructured_text,
                "language": payload.language,
                "instructions": payload.instructions,
                "previous_session_id": payload.previous_session_id,
                # Non-authoritative summary of previous session text for continuity only.
                "continuity_brief": continuity_brief,
                "previous_session_summary": continuity_brief,
            },
            "step_1_plan": {
                "system_prompt": plan_system_prompt,
                "user_prompt": plan_prompt,
                "raw_output": raw_plan,
                "parsed_output": parsed_plan,
            },
            "step_1_5_elder_query_planning": {
                "system_prompt": elder_query_plan_system_prompt,
                "user_prompt": elder_query_plan_user_prompt,
                "raw_output": raw_elder_query_plan,
                "parsed_output": elder_query_plan,
                # Elder context is flavor-only and non-authoritative.
                "per_part": elder_context_by_part,
            },
            "step_2_write_parts": {
                "system_prompt": part_system_prompt,
                "part_prompts": write_part_prompts,
                "part_outputs": draft_parts,
                "part_summaries": part_summaries,
                "elder_context_by_part": elder_context_by_part,
            },
            "step_3_critic": {
                "system_prompt": critic_system_prompt,
                "user_prompt": critic_user_prompt,
                "raw_output": raw_critic,
                "parsed_output": critic_notes,
            },
            "step_4_apply_critic": {
                "part_prompts": revise_part_prompts,
                "part_outputs": revised_parts,
                "coverage_system_prompt": coverage_system_prompt,
                "coverage_prompts": coverage_part_prompts,
                "coverage_reports": coverage_reports,
                "elder_context_by_part": elder_context_by_part,
            },
            "step_5_final_merge": {
                "final_text": final_text,
            },
        }

        return {
            "artifacts": artifacts,
            "draft_text": final_text,
            "critic_notes": json.dumps(critic_notes, ensure_ascii=True),
        }

    def _build_plan_user_prompt(
        self,
        *,
        unstructured_text: str,
        language: str,
        instructions: str,
        agent: Agent,
        continuity_brief: str,
    ) -> str:
        style_hint = f"\nWriter style: {agent.writing_style}" if agent.writing_style else ""
        continuity_block = continuity_brief or "None"
        return (
            f"Language: {language or 'match source'}\n"
            f"Instructions: {instructions or 'None'}{style_hint}\n\n"
            "Previous session context (for continuity only, NOT new events. Use this to enhance the events, "
            "if needed, in order to pick up events continuity that were important on the last session!):\n"
            f"{continuity_block}\n\n"
            f"Raw unstructured text:\n{unstructured_text}"
        )

    def _build_elder_query_plan_user_prompt(
        self,
        *,
        parsed_plan: dict[str, dict[str, Any]],
        source_context: str,
        continuity_brief: str,
        language: str,
        instructions: str,
        agent: Agent,
    ) -> str:
        style_hint = f"\nWriter style: {agent.writing_style}" if agent.writing_style else ""
        return (
            f"Language: {language or 'match source'}\n"
            f"Instructions: {instructions or 'None'}{style_hint}\n\n"
            "Assigned chapter plan (authoritative events):\n"
            f"{json.dumps(parsed_plan, ensure_ascii=True)}\n\n"
            "Frontend correction instructions (authoritative for names/terms):\n"
            f"{instructions or 'None'}\n\n"
            "Source excerpt (for grounding only):\n"
            f"{source_context}\n\n"
            "Previous session continuity brief (continuity only):\n"
            f"{continuity_brief or 'None'}\n\n"
            "Generate 2-5 targeted Elder questions for each part."
        )

    @staticmethod
    def _compose_system_prompt(base_prompt: str, *, language: str, instructions: str) -> str:
        language_rule = (
            f"- MANDATORY OUTPUT LANGUAGE: {language}. Every field and all prose must be in this language."
            if language
            else "- MANDATORY OUTPUT LANGUAGE: Match the source language unless explicitly instructed."
        )
        instructions_rule = (
            f"- MANDATORY INSTRUCTIONS TO FOLLOW: {instructions}"
            if instructions
            else "- MANDATORY INSTRUCTIONS TO FOLLOW: None."
        )
        return (
            f"{base_prompt}\n\nGlobal mandatory constraints:\n"
            f"{language_rule}\n"
            f"{instructions_rule}\n"
            "- If any instruction conflicts with these constraints, prioritize these constraints."
        )

    def _build_part_user_prompt(
        self,
        *,
        part_key: str,
        part_data: dict[str, Any],
        source_context: str,
        language: str,
        instructions: str,
        agent: Agent,
        previous_summaries: list[str],
        continuity_brief: str,
        elder_context_lines: list[str],
    ) -> str:
        style_hint = f"\nWriter style: {agent.writing_style}" if agent.writing_style else ""
        events = part_data.get("events") or []
        events_block = (
            "\n".join(f"{idx}. {b}" for idx, b in enumerate(events, start=1))
            if events
            else "1. No events provided."
        )
        summaries_block = "\n\n".join(previous_summaries) if previous_summaries else "None"
        continuity_block = continuity_brief or "None"
        elder_block = "\n".join(f"- {line}" for line in elder_context_lines) or "- None"
        return (
            f"Part: {part_key}\n"
            f"Part title: {part_data.get('title', part_key)}\n"
            f"Language: {language or 'match source'}\n"
            f"Instructions: {instructions or 'None'}{style_hint}\n\n"
            "Assigned events (strict timeline order):\n"
            f"{events_block}\n\n"
            "Short context from source (optional):\n"
            f"{source_context}\n\n"
            "Previous part summaries (context only, no reuse):\n"
            f"{summaries_block}\n\n"
            "Continuity context (use only for tone, memory, and references -- DO NOT introduce new events):\n"
            f"{continuity_block}\n\n"
            "Elder context (flavor only -- do NOT introduce new events):\n"
            f"{elder_block}\n\n"
            "Truth hierarchy (strict): assigned events > continuity brief > elder context.\n"
            "Elder context is NON-AUTHORITATIVE. It may enrich how text is written, never what happens.\n"
            "If Elder context conflicts with assigned events, ignore Elder context.\n"
            "If Elder context is not directly useful, ignore it.\n\n"
            "Coverage checklist: every assigned event must appear explicitly at least once.\n"
            "Write this part now with strict compliance."
        )

    def _build_critic_user_prompt(
        self,
        *,
        parsed_plan: dict[str, dict[str, Any]],
        draft_text: str,
        language: str,
        instructions: str,
    ) -> str:
        return (
            f"Language: {language or 'match source'}\n"
            f"Instructions: {instructions or 'None'}\n\n"
            "Assigned plan by part:\n"
            f"{json.dumps(parsed_plan, ensure_ascii=True)}\n\n"
            f"Draft to review:\n{draft_text}"
        )

    def _build_revise_user_prompt(
        self,
        *,
        part_key: str,
        part_data: dict[str, Any],
        part_text: str,
        part_critic_note: str,
        global_critic_note: str,
        remove_items: list[str],
        source_context: str,
        language: str,
        instructions: str,
        agent: Agent,
        continuity_brief: str,
        elder_context_lines: list[str],
    ) -> str:
        style_hint = f"\nWriter style: {agent.writing_style}" if agent.writing_style else ""
        events = part_data.get("events") or []
        events_block = (
            "\n".join(f"{idx}. {b}" for idx, b in enumerate(events, start=1))
            if events
            else "1. No events provided."
        )
        remove_block = "\n".join(f"- {item}" for item in remove_items) if remove_items else "- None"
        continuity_block = continuity_brief or "None"
        elder_block = "\n".join(f"- {line}" for line in elder_context_lines) or "- None"
        return (
            f"Part: {part_key}\n"
            f"Part title: {part_data.get('title', part_key)}\n"
            f"Language: {language or 'match source'}\n"
            f"Instructions: {instructions or 'None'}{style_hint}\n\n"
            "Assigned events for this part (do not add others):\n"
            f"{events_block}\n\n"
            "Must remove these overlap/invalid items:\n"
            f"{remove_block}\n\n"
            "Short source context:\n"
            f"{source_context}\n\n"
            "Continuity context (use only for tone, memory, and references -- DO NOT introduce new events):\n"
            f"{continuity_block}\n\n"
            "Elder context (flavor only -- do NOT introduce new events):\n"
            f"{elder_block}\n\n"
            "Truth hierarchy (strict): assigned events > continuity brief > elder context.\n"
            "Elder context is NON-AUTHORITATIVE. It may enrich how text is written, never what happens.\n"
            "If Elder context conflicts with assigned events, ignore Elder context.\n"
            "If Elder context is not directly useful, ignore it.\n\n"
            f"Current part text:\n{part_text}\n\n"
            f"Global critic notes:\n{global_critic_note}\n\n"
            f"Notes for this part:\n{part_critic_note}\n\n"
            "Coverage checklist: every assigned event must remain explicitly present.\n"
            "Return only revised HTML for this part."
        )

    async def _build_continuity_brief(
        self,
        *,
        previous_session_text: str,
        language: str,
        instructions: str,
        conversation_id: str | None = None,
    ) -> str:
        raw = re.sub(r"<[^>]+>", " ", previous_session_text or "")
        raw = re.sub(r"\s+", " ", raw).strip()
        if not raw:
            return ""
        system_prompt = self._compose_system_prompt(
            CONTINUITY_BRIEF_PROMPT, language=language, instructions=instructions
        )
        user_prompt = (
            "Previous session text:\n"
            f"{raw}\n\n"
            "Frontend correction instructions (authoritative for names/terms):\n"
            f"{instructions or 'None'}\n\n"
            "Return 5-8 short lines only."
        )
        try:
            continuity_raw = await self.llm_client.chat(
                model=self.critic_model or self.model_policy.get_model(LLMTask.VALIDATION),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                conversation_id=conversation_id,
            )
        except Exception:
            logger.warning("Failed to generate continuity brief via LLM", exc_info=True)
            return ""
        return self._normalize_continuity_brief(continuity_raw)

    @staticmethod
    def _normalize_continuity_brief(raw_brief: str) -> str:
        lines: list[str] = []
        for row in (raw_brief or "").splitlines():
            cleaned = re.sub(r"^\s*[-*0-9.)\s]+", "", row).strip()
            if not cleaned:
                continue
            lines.append(f"- {cleaned[:220]}")
        if not lines:
            compact = re.sub(r"\s+", " ", raw_brief or "").strip()
            if compact:
                lines = [f"- {compact[:220]}"]
        if len(lines) < 5:
            compact = re.sub(r"\s+", " ", raw_brief or "").strip()
            extra_chunks = [s.strip() for s in re.split(r"(?<=[.!?;])\s+", compact) if s.strip()]
            for chunk in extra_chunks:
                if len(lines) >= 5:
                    break
                candidate = f"- {chunk[:220]}"
                if candidate not in lines:
                    lines.append(candidate)
        return "\n".join(lines[:8])

    def _build_event_coverage_user_prompt(
        self,
        *,
        part_key: str,
        part_title: str,
        part_events: list[str],
        part_html: str,
        language: str,
        instructions: str,
        required_missing: list[str] | None = None,
    ) -> str:
        events_block = (
            "\n".join(f"{idx}. {event}" for idx, event in enumerate(part_events, start=1))
            if part_events
            else "1. No events provided."
        )
        required_block = (
            "\n".join(f"- {event}" for event in required_missing)
            if required_missing
            else "- None (validate full list anyway)"
        )
        return (
            f"Part: {part_key}\n"
            f"Part title: {part_title}\n"
            f"Language: {language or 'match source'}\n"
            f"Instructions: {instructions or 'None'}\n\n"
            "Assigned events (all must be present):\n"
            f"{events_block}\n\n"
            "Events explicitly required in this pass:\n"
            f"{required_block}\n\n"
            f"Current part HTML:\n{part_html}"
        )

    async def _collect_elder_context_by_part(
        self,
        *,
        agent: Agent,
        elder_query_plan: dict[str, list[str]],
    ) -> dict[str, dict[str, list[str]]]:
        part_keys = ("part_1", "part_2", "part_3")
        output: dict[str, dict[str, list[str]]] = {
            part_key: {
                "queries": self._normalize_text_list(
                    elder_query_plan.get(part_key, []), max_items=5
                ),
                "elder_context": [],
            }
            for part_key in part_keys
        }
        if not self.elder_query_runner:
            return output

        semaphore = asyncio.Semaphore(max(1, self.max_concurrency))

        async def run_query(part_key: str, query: str) -> tuple[str, str, list[dict[str, Any]]]:
            async with semaphore:
                try:
                    raw_chunks = await self.elder_query_runner(agent, query)
                except Exception:
                    logger.warning(
                        "Elder retrieval failed for part=%s query=%s",
                        part_key,
                        query,
                        exc_info=True,
                    )
                    raw_chunks = []
                chunks = [c for c in raw_chunks if isinstance(c, dict)]
                return part_key, query, chunks

        tasks = [
            asyncio.create_task(run_query(part_key, query))
            for part_key in part_keys
            for query in output[part_key]["queries"]
        ]
        if not tasks:
            return output

        query_results = await asyncio.gather(*tasks)
        per_part_results: dict[str, list[tuple[str, list[dict[str, Any]]]]] = {
            key: [] for key in part_keys
        }
        for part_key, query, chunks in query_results:
            per_part_results[part_key].append((query, chunks))

        for part_key in part_keys:
            output[part_key]["elder_context"] = self._compact_elder_context_lines(
                per_part_results.get(part_key, [])
            )
        return output

    def _parse_elder_query_plan(
        self,
        *,
        raw: str,
        parsed_plan: dict[str, dict[str, Any]],
    ) -> dict[str, list[str]]:
        part_keys = ("part_1", "part_2", "part_3")
        fallback = self._fallback_elder_queries_from_plan(parsed_plan)
        payload = self._parse_json_object(raw)
        data = payload if isinstance(payload, dict) else {}
        result: dict[str, list[str]] = {}
        for part_key in part_keys:
            part_block = data.get(part_key)
            part_payload = part_block if isinstance(part_block, dict) else {}
            queries = self._normalize_text_list(part_payload.get("queries", []), max_items=5)
            if len(queries) < 2:
                for candidate in fallback.get(part_key, []):
                    if candidate not in queries:
                        queries.append(candidate)
                    if len(queries) >= 2:
                        break
            result[part_key] = queries[:5]
        return result

    @staticmethod
    def _normalize_text_list(values: Any, *, max_items: int, max_chars: int = 220) -> list[str]:
        if not isinstance(values, list):
            return []
        items: list[str] = []
        for raw_value in values:
            compact = re.sub(r"\s+", " ", str(raw_value)).strip()
            if not compact:
                continue
            candidate = compact[:max_chars]
            if candidate in items:
                continue
            items.append(candidate)
            if len(items) >= max_items:
                break
        return items

    def _fallback_elder_queries_from_plan(
        self,
        parsed_plan: dict[str, dict[str, Any]],
    ) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for part_key in ("part_1", "part_2", "part_3"):
            events = parsed_plan.get(part_key, {}).get("events", [])
            event_list = self._normalize_text_list(events, max_items=3, max_chars=160)
            primary_event = event_list[0] if event_list else "the assigned scene events"
            secondary_event = event_list[1] if len(event_list) > 1 else primary_event
            out[part_key] = [
                (
                    "Which established personality or speaking-style trait should shape dialogue "
                    f"during '{primary_event}'?"
                ),
                (
                    "What prior relationship tension should influence reactions during "
                    f"'{primary_event}'?"
                ),
                (
                    "Which earlier exchange is most relevant to the emotional subtext in "
                    f"'{secondary_event}'?"
                ),
                (
                    "What known history of places, factions, or objects tied to "
                    f"'{primary_event}' should appear as texture only?"
                ),
                (
                    "What emotional or symbolic echo from the past can reinforce "
                    f"'{secondary_event}' without changing events?"
                ),
            ]
        return out

    def _compact_elder_context_lines(
        self,
        query_results: list[tuple[str, list[dict[str, Any]]]],
        *,
        max_lines: int = 8,
    ) -> list[str]:
        candidates: list[tuple[float, str]] = []
        for _query, chunks in query_results:
            top_chunks = chunks[:3]
            for chunk in top_chunks:
                text = self._compact_retrieved_text(str(chunk.get("text", "")))
                if not text:
                    continue
                score_raw = chunk.get("score", 0.0)
                try:
                    score = float(score_raw)
                except (TypeError, ValueError):
                    score = 0.0
                source_name = (
                    str(chunk.get("node_name") or "")
                    or str(chunk.get("node_alias") or "")
                    or str(chunk.get("node_label") or "")
                    or "Context"
                )
                source_name = re.sub(r"\s+", " ", source_name).strip()[:80]
                line = f"{source_name}: {text[:190]}"
                candidates.append((score, line))

        candidates.sort(key=lambda item: item[0], reverse=True)
        lines: list[str] = []
        seen: set[str] = set()
        for _, line in candidates:
            dedupe_key = line.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            lines.append(line)
            if len(lines) >= max_lines:
                break
        return lines[:max_lines]

    @staticmethod
    def _compact_retrieved_text(raw_text: str, max_chars: int = 200) -> str:
        compact = re.sub(r"<[^>]+>", " ", raw_text or "")
        compact = re.sub(r"\s+", " ", compact).strip()
        if not compact:
            return ""
        sentences = [s.strip() for s in re.split(r"(?<=[.!?;])\s+", compact) if s.strip()]
        seed = sentences[0] if sentences else compact
        return seed[:max_chars]

    @staticmethod
    def _parse_plan(raw: str) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(raw)
            return {
                k: NovelistOrchestrator._normalize_part_plan(data.get(k), default_title=f"Part {i}")
                for i, k in enumerate(("part_1", "part_2", "part_3"), start=1)
            }
        except Exception:
            return {
                "part_1": NovelistOrchestrator._normalize_part_plan(
                    {"title": "Beginning", "events": [raw.strip() or "Open the chapter events."]},
                    default_title="Part 1",
                ),
                "part_2": NovelistOrchestrator._normalize_part_plan(
                    {"title": "Middle", "events": ["Escalate conflict and consequences."]},
                    default_title="Part 2",
                ),
                "part_3": NovelistOrchestrator._normalize_part_plan(
                    {"title": "End", "events": ["Resolve the immediate arc clearly."]},
                    default_title="Part 3",
                ),
            }

    @staticmethod
    def _normalize_part_plan(raw_part: Any, *, default_title: str) -> dict[str, Any]:
        part = raw_part if isinstance(raw_part, dict) else {}
        events = part.get("events")
        if not isinstance(events, list):
            fallback = part.get("core_beats", [])
            events = fallback if isinstance(fallback, list) else []
        events = [str(event).strip() for event in events if str(event).strip()][:12]
        title = NovelistOrchestrator._normalize_part_title(
            raw_title=part.get("title"),
            events=events,
            default_title=default_title,
        )
        return {
            "title": title,
            "events": events,
        }

    @staticmethod
    def _prev_part_key(part_key: str) -> str | None:
        order = ("part_1", "part_2", "part_3")
        idx = order.index(part_key)
        return order[idx - 1] if idx > 0 else None

    @staticmethod
    def _next_part_key(part_key: str) -> str | None:
        order = ("part_1", "part_2", "part_3")
        idx = order.index(part_key)
        return order[idx + 1] if idx < len(order) - 1 else None

    @staticmethod
    def _parse_critic(raw: str) -> dict[str, Any]:
        try:
            data = json.loads(raw)
            return {
                "global_notes": str(data.get("global_notes", "")),
                "repeated_events": data.get("repeated_events", []),
                "timeline_issues": data.get("timeline_issues", []),
                "complexity_issues": data.get("complexity_issues", []),
                "part_1_notes": str(data.get("part_1_notes", "")),
                "part_2_notes": str(data.get("part_2_notes", "")),
                "part_3_notes": str(data.get("part_3_notes", "")),
                "remove_from_part_1": data.get("remove_from_part_1", []),
                "remove_from_part_2": data.get("remove_from_part_2", []),
                "remove_from_part_3": data.get("remove_from_part_3", []),
            }
        except Exception:
            return {
                "global_notes": raw.strip(),
                "repeated_events": [],
                "timeline_issues": [],
                "complexity_issues": [],
                "part_1_notes": "",
                "part_2_notes": "",
                "part_3_notes": "",
                "remove_from_part_1": [],
                "remove_from_part_2": [],
                "remove_from_part_3": [],
            }

    @staticmethod
    def _parse_event_coverage(coverage_raw: str, fallback_html: str) -> dict[str, Any]:
        data = NovelistOrchestrator._parse_json_object(coverage_raw)
        if not isinstance(data, dict):
            return {
                "missing_before": [],
                "missing_after": [],
                "revised_html": fallback_html,
                "raw_output": coverage_raw,
            }
        missing_before = data.get("missing_before", [])
        missing_after = data.get("missing_after", [])
        revised_html = str(data.get("revised_html", "")).strip() or fallback_html
        return {
            "missing_before": missing_before if isinstance(missing_before, list) else [],
            "missing_after": missing_after if isinstance(missing_after, list) else [],
            "revised_html": revised_html,
            "raw_output": coverage_raw,
        }

    @staticmethod
    def _parse_json_object(raw: str) -> Any:
        try:
            return json.loads(raw)
        except Exception:
            match = re.search(r"\{.*\}", raw or "", flags=re.DOTALL)
            if not match:
                return None
            try:
                return json.loads(match.group(0))
            except Exception:
                return None

    @staticmethod
    def _normalize_part_title(raw_title: Any, events: list[str], default_title: str) -> str:
        title = str(raw_title or "").strip()
        is_generic = bool(
            re.fullmatch(
                r"(?i)\s*(beginning|middle|end|part\s*[123]|part one|part two|part three)\s*",
                title,
            )
        )
        if not title or is_generic:
            return NovelistOrchestrator._derive_title_from_event(events, default_title)
        return title[:90]

    @staticmethod
    def _derive_title_from_event(events: list[str], default_title: str) -> str:
        if not events:
            return default_title
        seed = re.sub(r"[^a-zA-Z0-9\s]", "", events[0]).strip()
        if not seed:
            return default_title
        words = [w for w in seed.split() if w][:6]
        if not words:
            return default_title
        title = " ".join(words)
        return title[:90]

    @staticmethod
    def _short_source_context(unstructured_text: str, max_chars: int = 2200) -> str:
        compact = re.sub(r"\s+", " ", unstructured_text).strip()
        return compact[:max_chars]

    @staticmethod
    def _summarize_part_for_context(*, part_key: str, part_title: str, part_html: str) -> str:
        # Create a compact 3-line summary for downstream context; keep it short and factual.
        plain = re.sub(r"<[^>]+>", " ", part_html or "")
        plain = re.sub(r"\s+", " ", plain).strip()
        if not plain:
            return f"{part_key} - {part_title}\n1) No material generated.\n2) No material generated.\n3) No material generated."
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", plain) if s.strip()]
        if not sentences:
            sentences = [plain]
        lines = [f"{idx + 1}) {sentences[idx][:180]}" for idx in range(min(3, len(sentences)))]
        while len(lines) < 3:
            lines.append(f"{len(lines) + 1}) (no additional event)")
        return f"{part_key} - {part_title}\n" + "\n".join(lines)

    @staticmethod
    def _merge_parts_html(plan: dict[str, dict[str, str]], parts: dict[str, str]) -> str:
        blocks = []
        for idx, key in enumerate(("part_1", "part_2", "part_3"), start=1):
            title = plan.get(key, {}).get("title") or f"Part {idx}"
            text = (parts.get(key) or "").strip()
            blocks.append(f"<h2>Part {idx}: {title}</h2>\n{text}")
        return "\n\n".join(blocks).strip()

    @staticmethod
    def _ensure_readable_html(text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""

        if any(tag in raw.lower() for tag in ("<p", "<blockquote", "<ul", "<ol", "<h3", "<h4")):
            return raw

        chunks = [c.strip() for c in re.split(r"\n\s*\n+", raw) if c.strip()]
        if len(chunks) <= 1:
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", raw) if s.strip()]
            if not sentences:
                return f"<p>{raw}</p>"
            chunks = []
            for i in range(0, len(sentences), 3):
                chunks.append(" ".join(sentences[i : i + 3]).strip())

        html_blocks: list[str] = []
        for chunk in chunks:
            is_dialogue = (
                chunk.startswith('"')
                or chunk.startswith("'")
                or chunk.startswith("“")
                or chunk.startswith("—")
                or chunk.startswith("- ")
            )
            if is_dialogue:
                html_blocks.append(f"<blockquote>{chunk}</blockquote>")
            else:
                html_blocks.append(f"<p>{chunk}</p>")
        return "\n".join(html_blocks)
