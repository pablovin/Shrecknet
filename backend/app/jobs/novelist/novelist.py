"""Novelist orchestrator (step 1 draft pipeline)."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence
from html import unescape
from html.parser import HTMLParser

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.integrations.retrieval.neo4j_retriever import GraphRetriever
from app.jobs.elder.elder import ElderOrchestrator
from app.jobs.elder.schemas import ElderQueryRequest
from app.jobs.novelist.prompts import (
    CRITIC_PROMPT,
    ELDER_QUESTION_PROMPT,
    PART_PROMPT,
    PLAN_PROMPT,
)
from app.models.agent import Agent
from app.models.novelist import NovelistStage
from app.schemas.novelist import NovelistRunCreate, NovelistSource

logger = logging.getLogger(__name__)

StageCallback = Callable[[NovelistStage, dict[str, Any]], Awaitable[None]]

PART_NAMES = ("beginning", "climax", "conclusion")


class _HTMLTextExtractor(HTMLParser):
    """Extract visible text from HTML while preserving inline text content."""

    _BLOCK_TAGS = {
        "article",
        "section",
        "div",
        "p",
        "li",
        "ul",
        "ol",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "br",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def text(self) -> str:
        merged = "".join(self._parts)
        merged = re.sub(r"\n{3,}", "\n\n", merged)
        merged = re.sub(r"[ \t]{2,}", " ", merged)
        return merged.strip()


class NovelistOrchestrator:
    """Coordinates chunk QA, novelization, merge, and critic passes."""

    def __init__(
        self,
        *,
        llm_client: OpenAIClient,
        model_policy: ModelPolicy,
        graph_retriever: GraphRetriever,
        elder_orchestrator: ElderOrchestrator | None,
        max_concurrency: int = 4,
    ) -> None:
        self.llm_client = llm_client
        self.model_policy = model_policy
        self.graph_retriever = graph_retriever
        self.elder_orchestrator = elder_orchestrator
        # novelist-specific model preferences
        self.draft_model = getattr(model_policy, "model_novelist_draft", None)
        self.critic_model = getattr(model_policy, "model_novelist_critic", None)
        self.max_concurrency = max_concurrency

    async def execute(
        self,
        *,
        agent: Agent,
        payload: NovelistRunCreate,
        elder_agent: Agent | None,
        relevant_context_override: str | None = None,
        stage_callback: StageCallback | None = None,
    ) -> dict[str, Any]:
        language = payload.language
        instructions = payload.instructions
        # New pipeline: treat all sources as one combined input (no chunking).
        logger.info("Novelist: building combined input")
        chunk = self._build_combined_input(payload.sources)
        if not chunk:
            raise ValueError("No content to process")
        previous_session_summary = await self._extract_previous_session_summary(
            chunk.get("raw_preview", "")
        )
        chunk["previous_session_summary"] = previous_session_summary

        # Stage 1: relevant context
        logger.info("Novelist: stage=RELEVANT")
        relevant_context = (relevant_context_override or "").strip()
        chunk["relevant_context"] = relevant_context
        if stage_callback:
            await stage_callback(
                NovelistStage.RELEVANT,
                {
                    "relevant_context": relevant_context,
                    "previous_session_summary": previous_session_summary,
                },
            )

        # Stage 2: plan per chunk
        logger.info("Novelist: stage=PLANNING")
        semaphore = asyncio.Semaphore(self.max_concurrency)
        await self._plan_chunk(
            chunk=chunk,
            instructions=instructions,
            language=language,
            relevant_context=relevant_context,
            semaphore=semaphore,
        )
        if stage_callback:
            await stage_callback(NovelistStage.PLANNING, None)

        # Stage 2.5: enrich plan with elder context per part (optional)
        if elder_agent and self.elder_orchestrator:
            logger.info("Novelist: stage=ELDER_ENRICH")
            await self._enrich_plan_with_elder(
                chunk=chunk,
                elder_agent=elder_agent,
                semaphore=semaphore,
            )

        # Stage 3: write parts per chunk (parallel)
        logger.info("Novelist: stage=WRITING")
        await self._write_chunk_parts(
            chunk=chunk,
            instructions=instructions,
            language=language,
            relevant_context=relevant_context,
            novelist_prompt=payload.novelist_prompt,
            agent=agent,
            semaphore=semaphore,
        )
        if stage_callback:
            await stage_callback(NovelistStage.WRITING, None)

        # Stage 4: merge parts into chunk drafts, then merge chunks
        logger.info("Novelist: stage=MERGING")
        if stage_callback:
            await stage_callback(NovelistStage.MERGING, None)
        drafts = [chunk["draft"]] if chunk.get("draft") else []
        merged = await self._merge_chunks(
            drafts=drafts,
            instructions=instructions,
            language=language,
            agent=agent,
        )

        # Stage 5: critic + apply to parts in parallel, then re-merge
        logger.info("Novelist: stage=CRITIC")
        critic_notes = await self._critic_pass(
            draft=merged,
            critic_prompt=payload.critic_prompt,
        )
        if stage_callback:
            await stage_callback(
                NovelistStage.CRITIC,
                {"draft_text": merged, "critic_notes": critic_notes},
            )
        logger.info("Novelist: stage=APPLY_CRITIC")
        await self._apply_critic_to_chunk_parts(
            chunk=chunk,
            critic_notes=critic_notes,
            instructions=instructions,
            language=language,
            agent=agent,
            semaphore=semaphore,
        )
        if stage_callback:
            await stage_callback(
                NovelistStage.APPLY_CRITIC,
                {"critic_notes": critic_notes},
            )

        improved_drafts = [chunk["draft"]] if chunk.get("draft") else []
        improved = await self._merge_chunks(
            drafts=improved_drafts,
            instructions=instructions,
            language=language,
            agent=agent,
        )

        artifacts = self._build_artifacts(
            payload=payload,
            chunk=chunk,
            relevant_context=relevant_context,
            critic_notes=critic_notes,
            final_text=improved,
        )

        return {
            "artifacts": artifacts,
            "draft_text": improved,
            "critic_notes": critic_notes,
        }

    def _build_combined_input(
        self, sources: Sequence[NovelistSource | dict[str, Any]]
    ) -> dict[str, Any] | None:
        combined_parts: list[str] = []
        labels: list[str] = []
        for src in sources:
            text = self._load_source(src)
            label = self._get_source_value(src, "label")
            if label:
                labels.append(label)
            if text:
                combined_parts.append(text)
        combined_text = "\n\n".join(combined_parts).strip()
        if not combined_text:
            return None
        return {
            "index": 0,
            "source_label": ", ".join(labels) if labels else None,
            "raw_preview": combined_text,
            "source_clean_text": combined_text,
            "draft": None,
            "status": "pending",
        }

    def _load_source(self, source: NovelistSource | dict[str, Any]) -> str:
        kind = self._get_source_value(source, "kind") or "text"
        if kind == "text":
            return self._normalize_source_text(self._get_source_value(source, "content") or "")
        path_raw = self._get_source_value(source, "path")
        if not path_raw:
            return ""
        path = Path(path_raw)
        if not path.exists():
            raise FileNotFoundError(f"Source file not found: {path}")
        if path.suffix.lower() == ".txt":
            return self._normalize_source_text(path.read_text(encoding="utf-8", errors="ignore"))
        if path.suffix.lower() == ".pdf":
            try:
                from PyPDF2 import PdfReader
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise RuntimeError("PyPDF2 is required to read PDFs") from exc
            reader = PdfReader(str(path))
            pages: list[str] = []
            for page in reader.pages:
                try:
                    pages.append(page.extract_text() or "")
                except Exception:
                    continue
            return self._normalize_source_text("\n".join(pages))
        return self._normalize_source_text(path.read_text(encoding="utf-8", errors="ignore"))

    def _normalize_source_text(self, text: str) -> str:
        if not text:
            return ""
        stripped = text.strip()
        if "<" in stripped and ">" in stripped:
            parser = _HTMLTextExtractor()
            try:
                parser.feed(stripped)
                parser.close()
                cleaned = parser.text()
                if cleaned:
                    return cleaned
            except Exception:
                logger.warning("Novelist: HTML text normalization failed; falling back")
        return unescape(stripped)

    @staticmethod
    def _get_source_value(source: NovelistSource | dict[str, Any], key: str) -> Any:
        if isinstance(source, dict):
            return source.get(key)
        return getattr(source, key, None)

    async def _extract_previous_session_summary(self, previous_event_text: str) -> str:
        cleaned = (previous_event_text or "").strip()
        if not cleaned:
            return ""
        prompt = (
            "Summarize the previous session in 4-7 concise sentences. "
            "Keep only concrete events and character/location facts useful for continuity."
        )
        model = self.model_policy.get_model(LLMTask.VALIDATION)
        summary = await self.llm_client.chat(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": cleaned},
            ],
            temperature=0.2,
        )
        return (summary or "").strip()

    async def _plan_chunk(
        self,
        *,
        chunk: dict[str, Any],
        instructions: str | None,
        language: str | None,
        relevant_context: str,
        semaphore: asyncio.Semaphore,
    ) -> None:
        async with semaphore:
            prompt = PLAN_PROMPT
            context_block = self._build_context_block(
                instructions=instructions,
                language=language,
                relevant_context=relevant_context,
            )
            user_prompt = (
                f"{context_block}\n\n"
                f"Previous Session Summary:\n{chunk.get('previous_session_summary', '')}\n\n"
                "Create the plan now."
            )
            chunk["plan_prompt"] = {"system": prompt, "user": user_prompt}
            model = self.model_policy.get_model(LLMTask.VALIDATION)
            raw = await self.llm_client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.4,
            )
            plan, _unused_previous_summary = self._parse_three_part_plan(raw)
            chunk["plan"] = plan
            chunk["plan_steps"] = self._build_plan_steps(plan)
            chunk["status"] = "planned"

    async def _write_chunk_parts(
        self,
        *,
        chunk: dict[str, Any],
        instructions: str | None,
        language: str | None,
        relevant_context: str,
        novelist_prompt: str | None,
        agent: Agent,
        semaphore: asyncio.Semaphore,
    ) -> None:
        plan = chunk.get("plan") or {}
        plan_context = chunk.get("plan_context") or {}
        parts = {
            "beginning": plan.get("beginning", ""),
            "climax": plan.get("climax", ""),
            "conclusion": plan.get("conclusion", ""),
        }
        previous_summary = (chunk.get("previous_session_summary") or "").strip()

        async def _write_part(part_name: str, plan_text: str) -> str:
            async with semaphore:
                context_block = self._build_context_block(
                    instructions=instructions,
                    language=language,
                    relevant_context=relevant_context,
                    agent=agent,
                )
                elder_context = (plan_context.get(part_name) or "").strip()
                elder_block = (
                    f"\nElder context for this part:\n{elder_context}"
                    if elder_context
                    else ""
                )
                previous_block = (
                    f"\nPrevious Session Summary:\n{previous_summary}"
                    if previous_summary
                    else ""
                )
                user_prompt = (
                    f"{context_block}{previous_block}{elder_block}\n\n"
                    f"Part to write: {part_name}\nPlan: {plan_text}\n\n"
                    "Write the part now."
                )
                chunk.setdefault("part_prompts", {})[part_name] = {
                    "system": novelist_prompt or PART_PROMPT,
                    "user": user_prompt,
                }
                model = self.draft_model or self.model_policy.get_model(LLMTask.SYNTHESIS)
                return await self.llm_client.chat(
                    model=model,
                    messages=[
                        {"role": "system", "content": novelist_prompt or PART_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.9,
                )

        tasks = [
            _write_part("beginning", parts["beginning"]),
            _write_part("climax", parts["climax"]),
            _write_part("conclusion", parts["conclusion"]),
        ]
        written = await asyncio.gather(*tasks)
        part_drafts = {
            "beginning": written[0],
            "climax": written[1],
            "conclusion": written[2],
        }
        chunk["parts"] = part_drafts
        chunk["draft"] = self._merge_parts(
            parts=part_drafts,
            previous_summary=previous_summary,
        )
        chunk["status"] = "drafted"

    async def _enrich_plan_with_elder(
        self,
        *,
        chunk: dict[str, Any],
        elder_agent: Agent,
        semaphore: asyncio.Semaphore,
    ) -> None:
        plan = chunk.get("plan") or {}
        parts = {
            "beginning": plan.get("beginning", ""),
            "climax": plan.get("climax", ""),
            "conclusion": plan.get("conclusion", ""),
        }
        chunk.setdefault("elder_questions", {})

        async def _enrich(part_name: str, plan_text: str) -> tuple[str, str, list[str]]:
            if not plan_text:
                return "", "", []
            async with semaphore:
                questions = await self._generate_elder_questions(
                    part_name=part_name,
                    plan_text=plan_text,
                    source_text=chunk.get("raw_preview", ""),
                    previous_session_summary=chunk.get("previous_session_summary", ""),
                )
                chunk["elder_questions"][part_name] = questions
                plan_summary = self._summarize_plan_text(plan_text, limit=800)
                query = (
                    "Provide concise lore context to support writing this part. "
                    "If facts are unknown, say what is unknown.\n"
                    f"Part: {part_name}\n"
                    f"Plan summary: {plan_summary}"
                )
                if questions:
                    question_block = "\n".join(
                        f"{idx}. {q}" for idx, q in enumerate(questions, start=1)
                    )
                    query = f"{query}\n\nQuestions:\n{question_block}"
                query = self._truncate_query(query, limit=2000)
                logger.info(
                    "Novelist: elder query part=%s length=%d",
                    part_name,
                    len(query),
                )
                req = ElderQueryRequest(
                    query=query,
                    mode="both",
                    fast=True,
                    include_trace=False,
                )
                resp = await self.elder_orchestrator.execute(
                    elder_agent, req, chat_history=None
                )
                elder_answer = (resp.answer or "").strip()
                elder_context_text = elder_answer or self._format_elder_context(resp.context)
                return elder_context_text, elder_answer, questions

        tasks = [
            _enrich("beginning", parts["beginning"]),
            _enrich("climax", parts["climax"]),
            _enrich("conclusion", parts["conclusion"]),
        ]
        enriched = await asyncio.gather(*tasks)
        chunk["plan_context"] = {
            "beginning": enriched[0][0],
            "climax": enriched[1][0],
            "conclusion": enriched[2][0],
        }
        chunk["elder_responses"] = {
            "beginning": enriched[0][1],
            "climax": enriched[1][1],
            "conclusion": enriched[2][1],
        }

    @staticmethod
    def _truncate_query(text: str, *, limit: int) -> str:
        if len(text) <= limit:
            return text
        suffix = "\n[TRUNCATED]"
        keep = max(0, limit - len(suffix))
        return text[:keep] + suffix

    @staticmethod
    def _summarize_plan_text(text: str, *, limit: int) -> str:
        cleaned = (text or "").strip()
        if len(cleaned) <= limit:
            return cleaned
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        summary_lines: list[str] = []
        total = 0
        for line in lines:
            if total + len(line) + 1 > limit:
                break
            summary_lines.append(line)
            total += len(line) + 1
        if summary_lines:
            return "\n".join(summary_lines)
        return cleaned[:limit]

    def _format_elder_context(self, context: Sequence[Any]) -> str:
        if not context:
            return ""
        lines = ["Elder retrieved context:"]
        for idx, item in enumerate(context, start=1):
            name = getattr(item, "node_name", None) or "Unknown"
            label = getattr(item, "node_label", None) or "Entity"
            alias = getattr(item, "node_alias", None)
            alias_text = f" (alias: {alias})" if alias else ""
            text = (getattr(item, "text", "") or "").strip()
            if len(text) > 600:
                text = text[:600] + "..."
            lines.append(f"{idx}. {label}: {name}{alias_text}\n{text}")
        return "\n".join(lines)

    async def _apply_critic_to_chunk_parts(
        self,
        *,
        chunk: dict[str, Any],
        critic_notes: str,
        instructions: str | None,
        language: str | None,
        agent: Agent,
        semaphore: asyncio.Semaphore,
    ) -> None:
        parts = chunk.get("parts") or {}
        if not parts:
            return

        async def _apply(part_name: str, part_text: str) -> str:
            async with semaphore:
                return await self._apply_critic_to_part(
                    part_name=part_name,
                    part_text=part_text,
                    critic_notes=critic_notes,
                    instructions=instructions,
                    language=language,
                    agent=agent,
                )

        tasks = [
            _apply("beginning", parts.get("beginning", "")),
            _apply("climax", parts.get("climax", "")),
            _apply("conclusion", parts.get("conclusion", "")),
        ]
        improved = await asyncio.gather(*tasks)
        improved_parts = {
            "beginning": improved[0],
            "climax": improved[1],
            "conclusion": improved[2],
        }
        chunk["parts_improved"] = improved_parts
        chunk["draft"] = self._merge_parts(
            parts=improved_parts,
            previous_summary=(chunk.get("previous_session_summary") or "").strip(),
        )
        chunk["status"] = "revised"

    async def _apply_critic_to_part(
        self,
        *,
        part_name: str,
        part_text: str,
        critic_notes: str,
        instructions: str | None,
        language: str | None,
        agent: Agent,
    ) -> str:
        system_prompt = "Apply the critic notes to this part while keeping style consistent."
        style_hint = f"\nWriter style: {agent.writing_style}" if agent.writing_style else ""
        user_prompt = (
            f"Part: {part_name}\nLanguage: {language or 'match input'}\n"
            f"Instructions: {instructions or 'None'}{style_hint}\n\n"
            f"Part text:\n{part_text}\n\nCritic notes:\n{critic_notes}\n\n"
            "Return the revised part."
        )
        model = self.draft_model or self.model_policy.get_model(LLMTask.SYNTHESIS)
        return await self.llm_client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
        )

    def _merge_parts(self, parts: dict[str, str], previous_summary: str) -> str:
        previous_section = ""
        if previous_summary:
            previous_section = f"<h2>Previous Session</h2>\n{previous_summary.strip()}\n\n"
        part_sections = [
            f"<h2>Part 1: Beginning</h2>\n{parts.get('beginning', '').strip()}",
            f"<h2>Part 2: Climax</h2>\n{parts.get('climax', '').strip()}",
            f"<h2>Part 3: Conclusion</h2>\n{parts.get('conclusion', '').strip()}",
        ]
        return (previous_section + "\n\n".join(part_sections)).strip()

    def _build_context_block(
        self,
        *,
        instructions: str | None,
        language: str | None,
        relevant_context: str,
        agent: Agent | None = None,
    ) -> str:
        context_lines = []
        if instructions:
            context_lines.append(f"Instructions: {instructions}")
        if language:
            context_lines.append(f"Target language: {language}")
        if relevant_context:
            context_lines.append(relevant_context)
        if agent and agent.writing_style:
            context_lines.append(f"Writer style: {agent.writing_style}")
        return "\n".join(context_lines)

    def _parse_three_part_plan(self, raw: str) -> tuple[dict[str, str], str]:
        parts = {"beginning": "", "climax": "", "conclusion": ""}
        previous_summary = ""
        for line in raw.splitlines():
            stripped = line.strip()
            lower = stripped.lower()
            if lower.startswith("previous session summary:") or lower.startswith(
                "previous summary:"
            ):
                previous_summary = stripped.split(":", 1)[1].strip()
            elif lower.startswith("beginning:"):
                parts["beginning"] = stripped.split(":", 1)[1].strip()
            elif lower.startswith("climax:"):
                parts["climax"] = stripped.split(":", 1)[1].strip()
            elif lower.startswith("conclusion:"):
                parts["conclusion"] = stripped.split(":", 1)[1].strip()
            elif lower.startswith("solution:") and not parts["conclusion"]:
                parts["conclusion"] = stripped.split(":", 1)[1].strip()
            elif lower.startswith("resolution:") and not parts["conclusion"]:
                parts["conclusion"] = stripped.split(":", 1)[1].strip()
        if not any(parts.values()):
            parts["beginning"] = raw.strip()
        return parts, previous_summary

    def _build_plan_steps(self, plan: dict[str, str]) -> dict[str, list[str]]:
        steps: dict[str, list[str]] = {}
        for part_name in PART_NAMES:
            text = (plan.get(part_name) or "").strip()
            if not text:
                steps[part_name] = []
                continue
            lines = [line.strip(" -\t") for line in text.splitlines() if line.strip()]
            if len(lines) <= 1:
                segments = [seg.strip() for seg in re.split(r"(?<=[.!?])\s+", text) if seg.strip()]
                lines = segments if segments else [text]
            steps[part_name] = lines[:8]
        return steps

    def _build_artifacts(
        self,
        *,
        payload: NovelistRunCreate,
        chunk: dict[str, Any],
        relevant_context: str,
        critic_notes: str,
        final_text: str,
    ) -> dict[str, Any]:
        return {
            "inputs": {
                "language": payload.language,
                "instructions": payload.instructions,
                "sources": self._serialize_sources(payload.sources),
                "relevant_instance_ids": payload.relevant_instance_ids,
            },
            "relevant_context": relevant_context,
            "plan_prompt": chunk.get("plan_prompt"),
            "plan_response": chunk.get("plan"),
            "plan_steps": chunk.get("plan_steps"),
            "previous_session_summary": chunk.get("previous_session_summary"),
            "elder_questions": chunk.get("elder_questions"),
            "elder_context_text": chunk.get("plan_context"),
            "elder_responses": chunk.get("elder_responses"),
            "part_prompts": chunk.get("part_prompts"),
            "part_responses": chunk.get("parts"),
            "critic_notes": critic_notes,
            "final_text": final_text,
        }

    async def _generate_elder_questions(
        self,
        *,
        part_name: str,
        plan_text: str,
        source_text: str,
        previous_session_summary: str,
    ) -> list[str]:
        if not plan_text:
            return []
        model = self.model_policy.get_model(LLMTask.VALIDATION)
        user_prompt = (
            f"Part: {part_name}\n"
            f"Previous Session Summary:\n{previous_session_summary}\n\n"
            f"Source text already known:\n{source_text}\n\n"
            f"Plan:\n{plan_text}\n\nCreate the questions now."
        )
        raw = await self.llm_client.chat(
            model=model,
            messages=[
                {"role": "system", "content": ELDER_QUESTION_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
        questions: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped[0].isdigit():
                stripped = stripped.lstrip("0123456789. )-").strip()
            if stripped:
                questions.append(stripped)
        return questions[:5]

    @staticmethod
    def _serialize_sources(
        sources: Sequence[NovelistSource | dict[str, Any]]
    ) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for source in sources:
            if isinstance(source, dict):
                serialized.append(source)
            else:
                serialized.append(source.model_dump())
        return serialized

    async def _merge_chunks(
        self,
        *,
        drafts: list[str],
        instructions: str | None,
        language: str | None,
        agent: Agent,
    ) -> str:
        if len(drafts) == 1:
            return drafts[0]
        system_prompt = (
            "You merge multiple scene fragments into one coherent narrative while keeping voice consistent."
        )
        pieces = "\n\n---\n\n".join(drafts)
        style_hint = f"\nWriter style: {agent.writing_style}" if agent.writing_style else ""
        user_prompt = (
            f"Language: {language or 'match input'}\nInstructions: {instructions or 'None'}{style_hint}\n"
            f"Merge the following fragments into one continuous narrative with smooth transitions:\n{pieces}"
        )
        model = self.draft_model or self.model_policy.get_model(LLMTask.SYNTHESIS)
        return await self.llm_client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
        )

    async def _critic_pass(
        self,
        *,
        draft: str,
        critic_prompt: str | None,
    ) -> str:
        prompt = critic_prompt or CRITIC_PROMPT
        from app.integrations.llm.model_policy import LLMTask

        model = self.critic_model or self.model_policy.get_model(LLMTask.VALIDATION)
        return await self.llm_client.chat(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": draft},
            ],
            temperature=0.3,
        )

    async def _apply_critic(
        self,
        *,
        draft: str,
        critic_notes: str,
        instructions: str | None,
        language: str | None,
        agent: Agent,
    ) -> str:
        system_prompt = "Apply the requested fixes faithfully while keeping style consistent."
        style_hint = f"\nWriter style: {agent.writing_style}" if agent.writing_style else ""
        user_prompt = (
            f"Language: {language or 'match input'}\nInstructions: {instructions or 'None'}{style_hint}\n"
            f"Original draft:\n{draft}\n\nCritic notes:\n{critic_notes}\n\nApply changes and return the improved text."
        )
        from app.integrations.llm.model_policy import LLMTask

        model = self.draft_model or self.model_policy.get_model(LLMTask.SYNTHESIS)
        return await self.llm_client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
        )
