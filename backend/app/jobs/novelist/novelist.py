"""Simplified Novelist orchestrator."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Awaitable, Callable

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.jobs.novelist.prompts import CRITIC_PROMPT, PART_PROMPT, PLAN_PROMPT
from app.models.agent import Agent
from app.models.novelist import NovelistStage
from app.schemas.novelist import NovelistRunCreate

logger = logging.getLogger(__name__)

StageCallback = Callable[[NovelistStage, dict[str, Any]], Awaitable[None]]


class NovelistOrchestrator:
    """Minimal chapter pipeline: plan -> write -> critic -> revise -> merge."""

    def __init__(
        self,
        *,
        llm_client: OpenAIClient,
        model_policy: ModelPolicy,
        max_concurrency: int = 4,
    ) -> None:
        self.llm_client = llm_client
        self.model_policy = model_policy
        self.draft_model = getattr(model_policy, "model_novelist_draft", None)
        self.critic_model = getattr(model_policy, "model_novelist_critic", None)
        self.max_concurrency = max_concurrency

    async def execute(
        self,
        *,
        agent: Agent,
        payload: NovelistRunCreate,
        stage_callback: StageCallback | None = None,
    ) -> dict[str, Any]:
        unstructured_text = payload.unstructured_text.strip()
        language = (payload.language or "").strip()
        instructions = (payload.instructions or "").strip()

        # Stage 1: Planning
        plan_prompt = self._build_plan_user_prompt(
            unstructured_text=unstructured_text,
            language=language,
            instructions=instructions,
            agent=agent,
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
        )
        parsed_plan = self._parse_plan(raw_plan)
        if stage_callback:
            await stage_callback(
                NovelistStage.PLANNING,
                {
                    "plan_raw": raw_plan,
                    "plan_parsed": parsed_plan,
                },
            )

        # Stage 2: Write parts in parallel
        semaphore = asyncio.Semaphore(self.max_concurrency)
        write_part_prompts: dict[str, dict[str, str]] = {}

        async def _write_part(part_key: str) -> str:
            async with semaphore:
                part_data = parsed_plan[part_key]
                user_prompt = self._build_part_user_prompt(
                    part_key=part_key,
                    part_data=part_data,
                    unstructured_text=unstructured_text,
                    language=language,
                    instructions=instructions,
                    agent=agent,
                    previous_part_title=parsed_plan.get(self._prev_part_key(part_key), {}).get("title", ""),
                    next_part_title=parsed_plan.get(self._next_part_key(part_key), {}).get("title", ""),
                )
                write_part_prompts[part_key] = {
                    "system_prompt": self._compose_system_prompt(
                        PART_PROMPT, language=language, instructions=instructions
                    ),
                    "user_prompt": user_prompt,
                }
                return await self.llm_client.chat(
                    model=self.draft_model or self.model_policy.get_model(LLMTask.SYNTHESIS),
                    messages=[
                        {"role": "system", "content": write_part_prompts[part_key]["system_prompt"]},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.9,
                )

        part_keys = ("part_1", "part_2", "part_3")
        draft_parts_values = await asyncio.gather(*[_write_part(key) for key in part_keys])
        draft_parts = {
            k: self._ensure_readable_html(v) for k, v in zip(part_keys, draft_parts_values)
        }

        if stage_callback:
            await stage_callback(NovelistStage.WRITING, {"parts": draft_parts})

        # Stage 3: Critic on merged draft
        pre_critic_draft = self._merge_parts_html(parsed_plan, draft_parts)
        critic_user_prompt = self._build_critic_user_prompt(
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
        )
        critic_notes = self._parse_critic(raw_critic)
        if stage_callback:
            await stage_callback(
                NovelistStage.CRITIC,
                {"draft_text": pre_critic_draft, "critic_notes": critic_notes},
            )

        # Stage 4: Apply critic to each part in parallel
        revise_part_prompts: dict[str, dict[str, str]] = {}

        async def _revise_part(part_key: str) -> str:
            async with semaphore:
                part_data = parsed_plan[part_key]
                part_text = draft_parts.get(part_key, "")
                part_note = critic_notes.get(f"{part_key}_notes", "")
                user_prompt = self._build_revise_user_prompt(
                    part_key=part_key,
                    part_data=part_data,
                    part_text=part_text,
                    part_critic_note=part_note,
                    global_critic_note=critic_notes.get("global_notes", ""),
                    unstructured_text=unstructured_text,
                    language=language,
                    instructions=instructions,
                    agent=agent,
                    previous_part_title=parsed_plan.get(self._prev_part_key(part_key), {}).get("title", ""),
                    next_part_title=parsed_plan.get(self._next_part_key(part_key), {}).get("title", ""),
                )
                revise_system_prompt = self._compose_system_prompt(
                    (
                        "Revise this chapter part using critic notes while preserving style and continuity. "
                        "Return valid HTML only, using <p> for narrative paragraphs, <blockquote> for spoken dialogue, "
                        "and <strong>/<em> for meaningful emphasis."
                    ),
                    language=language,
                    instructions=instructions,
                )
                revise_part_prompts[part_key] = {
                    "system_prompt": revise_system_prompt,
                    "user_prompt": user_prompt,
                }
                return await self.llm_client.chat(
                    model=self.draft_model or self.model_policy.get_model(LLMTask.SYNTHESIS),
                    messages=[
                        {
                            "role": "system",
                            "content": revise_system_prompt,
                        },
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.6,
                )

        revised_parts_values = await asyncio.gather(*[_revise_part(key) for key in part_keys])
        revised_parts = {
            k: self._ensure_readable_html(v) for k, v in zip(part_keys, revised_parts_values)
        }
        if stage_callback:
            await stage_callback(
                NovelistStage.APPLY_CRITIC,
                {"critic_notes": critic_notes, "parts_revised": revised_parts},
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
            },
            "step_1_plan": {
                "system_prompt": plan_system_prompt,
                "user_prompt": plan_prompt,
                "raw_output": raw_plan,
                "parsed_output": parsed_plan,
            },
            "step_2_write_parts": {
                "system_prompt": self._compose_system_prompt(
                    PART_PROMPT, language=language, instructions=instructions
                ),
                "part_prompts": write_part_prompts,
                "part_outputs": draft_parts,
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
    ) -> str:
        style_hint = f"\nWriter style: {agent.writing_style}" if agent.writing_style else ""
        return (
            f"Language: {language or 'match source'}\n"
            f"Instructions: {instructions or 'None'}{style_hint}\n\n"
            f"Raw unstructured text:\n{unstructured_text}"
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
        unstructured_text: str,
        language: str,
        instructions: str,
        agent: Agent,
        previous_part_title: str,
        next_part_title: str,
    ) -> str:
        style_hint = f"\nWriter style: {agent.writing_style}" if agent.writing_style else ""
        plan_block = self._format_part_plan_for_writer(part_data)
        return (
            f"Part: {part_key}\n"
            f"Part title: {part_data.get('title', part_key)}\n"
            f"Previous part title: {previous_part_title or 'None'}\n"
            f"Next part title: {next_part_title or 'None'}\n"
            f"Language: {language or 'match source'}\n"
            f"Instructions: {instructions or 'None'}{style_hint}\n\n"
            f"Part plan:\n{plan_block}\n\n"
            "Critical boundary rule: write only this part's beats; do not repeat previous beats and do not pre-write next-part beats.\n\n"
            f"Raw unstructured text:\n{unstructured_text}\n\n"
            "Write this part now (max 12 paragraphs)."
        )

    def _build_critic_user_prompt(
        self,
        *,
        draft_text: str,
        language: str,
        instructions: str,
    ) -> str:
        return (
            f"Language: {language or 'match source'}\n"
            f"Instructions: {instructions or 'None'}\n\n"
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
        unstructured_text: str,
        language: str,
        instructions: str,
        agent: Agent,
        previous_part_title: str,
        next_part_title: str,
    ) -> str:
        style_hint = f"\nWriter style: {agent.writing_style}" if agent.writing_style else ""
        plan_block = self._format_part_plan_for_writer(part_data)
        return (
            f"Part: {part_key}\n"
            f"Part title: {part_data.get('title', part_key)}\n"
            f"Previous part title: {previous_part_title or 'None'}\n"
            f"Next part title: {next_part_title or 'None'}\n"
            f"Language: {language or 'match source'}\n"
            f"Instructions: {instructions or 'None'}{style_hint}\n\n"
            f"Part plan:\n{plan_block}\n\n"
            "Critical boundary rule: revise only this part's beats; avoid overlap with adjacent parts.\n\n"
            f"Source raw text:\n{unstructured_text}\n\n"
            f"Current part text:\n{part_text}\n\n"
            f"Global critic notes:\n{global_critic_note}\n\n"
            f"Notes for this part:\n{part_critic_note}\n\n"
            "Return only valid HTML for the revised part (max 12 paragraphs), using <p>, <blockquote>, and optional <strong>/<em>."
        )

    @staticmethod
    def _parse_plan(raw: str) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(raw)
            return {k: NovelistOrchestrator._normalize_part_plan(data.get(k), default_title=f"Part {i}") for i, k in enumerate(("part_1", "part_2", "part_3"), start=1)}
        except Exception:
            return {
                "part_1": NovelistOrchestrator._normalize_part_plan({"title": "Part 1", "scope": raw.strip(), "core_beats": [raw.strip()]}, default_title="Part 1"),
                "part_2": NovelistOrchestrator._normalize_part_plan({"title": "Part 2", "scope": "Continue naturally from part 1.", "core_beats": ["Escalate conflict and consequences."]}, default_title="Part 2"),
                "part_3": NovelistOrchestrator._normalize_part_plan({"title": "Part 3", "scope": "Conclude the chapter coherently.", "core_beats": ["Resolve immediate arc and close with a hook."]}, default_title="Part 3"),
            }

    @staticmethod
    def _normalize_part_plan(raw_part: Any, *, default_title: str) -> dict[str, Any]:
        part = raw_part if isinstance(raw_part, dict) else {}
        beats = part.get("core_beats")
        if not isinstance(beats, list):
            beats = [str(part.get("plan", "")).strip()] if part.get("plan") else []
        beats = [str(b).strip() for b in beats if str(b).strip()][:8]
        return {
            "title": str(part.get("title", default_title)),
            "scope": str(part.get("scope", part.get("plan", ""))),
            "tone": str(part.get("tone", "")),
            "focus": str(part.get("focus", "")),
            "pacing": str(part.get("pacing", "")),
            "writing_goal": str(part.get("writing_goal", "")),
            "core_beats": beats,
        }

    @staticmethod
    def _format_part_plan_for_writer(part_data: dict[str, Any]) -> str:
        beats = part_data.get("core_beats") or []
        beats_block = "\n".join(f"{idx}. {b}" for idx, b in enumerate(beats, start=1)) if beats else "1. Follow the part scope faithfully."
        return (
            f"Scope: {part_data.get('scope', '')}\n"
            f"Tone: {part_data.get('tone', '')}\n"
            f"Focus: {part_data.get('focus', '')}\n"
            f"Pacing: {part_data.get('pacing', '')}\n"
            f"Writing goal: {part_data.get('writing_goal', '')}\n"
            f"Core beats (ordered):\n{beats_block}"
        )

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
    def _parse_critic(raw: str) -> dict[str, str]:
        try:
            data = json.loads(raw)
            return {
                "global_notes": str(data.get("global_notes", "")),
                "part_1_notes": str(data.get("part_1_notes", "")),
                "part_2_notes": str(data.get("part_2_notes", "")),
                "part_3_notes": str(data.get("part_3_notes", "")),
            }
        except Exception:
            return {
                "global_notes": raw.strip(),
                "part_1_notes": "",
                "part_2_notes": "",
                "part_3_notes": "",
            }

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
