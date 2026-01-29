"""Novelist orchestrator (step 1 draft pipeline)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.integrations.retrieval.neo4j_retriever import GraphRetriever
from app.jobs.elder.elder import ElderOrchestrator
from app.jobs.elder.schemas import ElderQueryRequest
from app.jobs.novelist.prompts import (
    CRITIC_PROMPT,
    NOVELIST_CHUNK_PROMPT,
    QUESTION_PROMPT,
)
from app.models.agent import Agent
from app.models.novelist import NovelistStage
from app.schemas.novelist import NovelistRunCreate

logger = logging.getLogger(__name__)

StageCallback = Callable[[NovelistStage, dict[str, Any]], Awaitable[None]]


class NovelistOrchestrator:
    """Coordinates chunk QA, novelization, merge, and critic passes."""

    def __init__(
        self,
        *,
        llm_client: OpenAIClient,
        model_policy: ModelPolicy,
        graph_retriever: GraphRetriever,
        elder_orchestrator: ElderOrchestrator | None,
        default_chunk_size: int = 2000,
        default_max_chunks: int = 4,
        default_questions_per_chunk: int = 10,
        max_concurrency: int = 4,
    ) -> None:
        self.llm_client = llm_client
        self.model_policy = model_policy
        self.graph_retriever = graph_retriever
        self.elder_orchestrator = elder_orchestrator
        # novelist-specific model preferences
        self.draft_model = getattr(model_policy, "model_novelist_draft", None)
        self.critic_model = getattr(model_policy, "model_novelist_critic", None)
        self.default_chunk_size = default_chunk_size
        self.default_max_chunks = default_max_chunks
        self.default_questions_per_chunk = default_questions_per_chunk
        self.max_concurrency = max_concurrency

    async def execute(
        self,
        *,
        agent: Agent,
        payload: NovelistRunCreate,
        elder_agent: Agent | None,
        stage_callback: StageCallback | None = None,
    ) -> dict[str, Any]:
        language = payload.language
        instructions = payload.instructions
        chunk_size = payload.chunk_size or self.default_chunk_size
        max_chunks = payload.max_chunks or self.default_max_chunks
        q_per_chunk = payload.questions_per_chunk or self.default_questions_per_chunk

        chunks = self._build_chunks(payload.sources, chunk_size=chunk_size, max_chunks=max_chunks)
        if not chunks:
            raise ValueError("No content to process")

        # Stage 1: questions
        semaphore = asyncio.Semaphore(self.max_concurrency)
        await asyncio.gather(
            *[
                self._generate_questions_for_chunk(
                    chunk=chunk,
                    instructions=instructions,
                    questions_per_chunk=q_per_chunk,
                    semaphore=semaphore,
                )
                for chunk in chunks
            ]
        )
        if stage_callback:
            await stage_callback(NovelistStage.QUESTIONS, {"chunks": chunks})

        # Stage 2: answers via elder agent (if provided)
        if elder_agent and self.elder_orchestrator:
            await asyncio.gather(
                *[
                    self._answer_questions_for_chunk(
                        chunk=chunk,
                        elder_agent=elder_agent,
                        semaphore=semaphore,
                    )
                    for chunk in chunks
                ]
            )
        if stage_callback:
            await stage_callback(NovelistStage.ANSWERS, {"chunks": chunks})

        # Stage 3: draft per chunk
        await asyncio.gather(
            *[
                self._draft_chunk(
                    chunk=chunk,
                    instructions=instructions,
                    language=language,
                    novelist_prompt=payload.novelist_prompt,
                    agent=agent,
                    semaphore=semaphore,
                )
                    for chunk in chunks
                ]
        )
        if stage_callback:
            await stage_callback(NovelistStage.DRAFTING, {"chunks": chunks})

        # Stage 4: merge
        if stage_callback:
            await stage_callback(NovelistStage.MERGING, {"chunks": chunks})
        drafts = [c["draft"] for c in chunks if c.get("draft")]
        merged = await self._merge_chunks(
            drafts=drafts,
            instructions=instructions,
            language=language,
            agent=agent,
        )

        # Stage 5: critic + apply
        critic_notes = await self._critic_pass(
            draft=merged,
            critic_prompt=payload.critic_prompt,
        )
        if stage_callback:
            await stage_callback(
                NovelistStage.CRITIC,
                {"chunks": chunks, "draft_text": merged, "critic_notes": critic_notes},
            )
        improved = await self._apply_critic(
            draft=merged,
            critic_notes=critic_notes,
            instructions=instructions,
            language=language,
            agent=agent,
        )

        return {
            "chunks": chunks,
            "draft_text": improved,
            "critic_notes": critic_notes,
        }

    def _build_chunks(
        self, sources: Sequence[dict[str, Any]], *, chunk_size: int, max_chunks: int
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for src in sources:
            text = self._load_source(src)
            label = src.get("label")
            words = text.split()
            for start in range(0, len(words), chunk_size):
                if len(entries) >= max_chunks:
                    break
                part = " ".join(words[start : start + chunk_size])
                entries.append(
                    {
                        "index": len(entries),
                        "source_label": label,
                        "raw_preview": part,
                        "questions": [],
                        "answers": [],
                        "draft": None,
                        "status": "pending",
                    }
                )
        return entries

    def _load_source(self, source: dict[str, Any]) -> str:
        kind = source.get("kind") or "text"
        if kind == "text":
            return source.get("content") or ""
        path_raw = source.get("path")
        if not path_raw:
            return ""
        path = Path(path_raw)
        if not path.exists():
            raise FileNotFoundError(f"Source file not found: {path}")
        if path.suffix.lower() == ".txt":
            return path.read_text(encoding="utf-8", errors="ignore")
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
            return "\n".join(pages)
        return path.read_text(encoding="utf-8", errors="ignore")

    async def _generate_questions_for_chunk(
        self,
        *,
        chunk: dict[str, Any],
        instructions: str | None,
        questions_per_chunk: int,
        semaphore: asyncio.Semaphore,
    ) -> None:
        async with semaphore:
            system_prompt = QUESTION_PROMPT
            user_prompt = (
                f"Text:\n{chunk['raw_preview'][:4000]}\n\n"
                f"Instructions:\n{instructions or 'None'}\n\n"
                f"Create up to {questions_per_chunk} short questions that would clarify context and characters."
            )
            model = self.model_policy.get_model(LLMTask.VALIDATION)
            raw = await self.llm_client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
            )
            questions: list[str] = []
            for line in raw.splitlines():
                line = line.strip(" -*\t")
                if line:
                    questions.append(line)
            chunk["questions"] = questions[:questions_per_chunk]
            chunk["status"] = "questions"

    async def _answer_questions_for_chunk(
        self,
        *,
        chunk: dict[str, Any],
        elder_agent: Agent,
        semaphore: asyncio.Semaphore,
    ) -> None:
        if not chunk.get("questions"):
            return

        async with semaphore:
            answers: list[str] = []
            for question in chunk["questions"]:
                req = ElderQueryRequest(
                    query=question,
                    mode="nl",
                    fast=True,
                    include_trace=False,
                )
                resp = await self.elder_orchestrator.execute(
                    elder_agent,
                    req,
                    chat_history=None,
                )
                answers.append(resp.answer or "")
            chunk["answers"] = answers
            chunk["status"] = "answered"

    async def _draft_chunk(
        self,
        *,
        chunk: dict[str, Any],
        instructions: str | None,
        language: str | None,
        novelist_prompt: str | None,
        agent: Agent,
        semaphore: asyncio.Semaphore,
    ) -> None:
        async with semaphore:
            prompt = novelist_prompt or NOVELIST_CHUNK_PROMPT
            context_lines = []
            if instructions:
                context_lines.append(f"Instructions: {instructions}")
            if language:
                context_lines.append(f"Target language: {language}")
            if chunk.get("answers"):
                context_lines.append("Clarifications:")
                context_lines.extend(f"- {a}" for a in chunk["answers"])
            if agent.writing_style:
                context_lines.append(f"Writer style: {agent.writing_style}")
            context_block = "\n".join(context_lines)
            user_prompt = (
                f"{context_block}\n\nSource block:\n{chunk['raw_preview']}\n\nWrite the novelized text now."
            )
            model = self.draft_model or self.model_policy.get_model(LLMTask.SYNTHESIS)
            draft = await self.llm_client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.9,
            )
            chunk["draft"] = draft
            chunk["status"] = "drafted"

    async def _merge_chunks(
        self,
        *,
        drafts: list[str],
        instructions: str | None,
        language: str | None,
        agent: Agent,
    ) -> str:
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
