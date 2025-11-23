"""Celery task for Novelist draft generation (step 1)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.celery_app import celery_app
from app.core.config import get_settings
from app.integrations.llm.openai_client import OpenAIClient
from app.models.background_job import AuthorType, JobType
from app.models.novelist import NovelistRunStatus, NovelistStage
from app.repositories.agent_repository import AgentRepository
from app.repositories.novelist_repository import NovelistRepository
from app.schemas.novelist import NovelistRunCreate
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)
from app.db.session import AsyncSessionMaker

logger = logging.getLogger(__name__)
settings = get_settings()

DEFAULT_NOVELIST_PROMPT = """You are a careful novelist. Follow these rules:
1. Fictionalize exclusively the block I'm about to give you.
2. Treat it as an isolated fragment: no references or repetitions from outside the block.
3. Remove all meta-game elements; keep everything diegetic.
4. Always use the correct character names; never player names.
5. Keep narrative style, tone, terminology, and characterization consistent with provided context.
6. Balance atmosphere and dialogue evenly.
7. Do not invent new elements; only minimal connectors for fluency.
8. Use long, flowing sentences; no colons; English quotation marks; consistent voice."""

DEFAULT_CRITIC_PROMPT = """You are a narrative critic. Review the story for:
- Consistency of characters, tone, and setting
- Continuity issues between chunks
- Clarity and pacing improvements
- Places where dialogue or atmosphere should be adjusted
Return a concise bullet list of problems and suggested fixes."""


def _chunk_words(text: str, *, chunk_size: int, max_chunks: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks: list[list[str]] = []
    for start in range(0, len(words), chunk_size):
        if len(chunks) >= max_chunks:
            break
        end = start + chunk_size
        chunks.append(words[start:end])
    return [" ".join(c) for c in chunks]


def _load_source(source: dict[str, Any]) -> str:
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
    # Fallback: try plain read
    return path.read_text(encoding="utf-8", errors="ignore")


async def _llm_chat(
    client: OpenAIClient, *, model: str, system: str, user: str, temperature: float = 0.7
) -> str:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return await client.chat(model=model, messages=messages, temperature=temperature)


async def _generate_questions(
    client: OpenAIClient,
    *,
    chunk: str,
    instructions: str | None,
    questions_per_chunk: int,
    model: str,
) -> list[str]:
    system_prompt = "Generate concise clarification questions to better understand the text."
    user_prompt = (
        f"Text:\n{chunk[:4000]}\n\n"
        f"Instructions:\n{instructions or 'None'}\n\n"
        f"Create up to {questions_per_chunk} short questions that would clarify context and characters."
    )
    raw = await _llm_chat(client, model=model, system=system_prompt, user=user_prompt)
    # Simple split by newline / bullets
    questions: list[str] = []
    for line in raw.splitlines():
        line = line.strip(" -*\t")
        if not line:
            continue
        questions.append(line)
    return questions[:questions_per_chunk]


async def _answer_questions(
    client: OpenAIClient,
    *,
    chunk: str,
    questions: list[str],
    instructions: str | None,
    model: str,
) -> list[str]:
    if not questions:
        return []
    joined = "\n".join(f"- {q}" for q in questions)
    system_prompt = "Answer as a helpful lore keeper with concise context-grounded replies."
    user_prompt = (
        f"Context:\n{chunk[:4000]}\n\nInstructions:\n{instructions or 'None'}\n\n"
        f"Answer these questions based only on the context above:\n{joined}"
    )
    raw = await _llm_chat(client, model=model, system=system_prompt, user=user_prompt)
    answers: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        answers.append(line.strip())
    if not answers:
        answers = [raw.strip()] if raw.strip() else []
    return answers[: len(questions)]


async def _novelize_chunk(
    client: OpenAIClient,
    *,
    chunk: str,
    instructions: str | None,
    language: str | None,
    novelist_prompt: str | None,
    answers: list[str],
    model: str,
) -> str:
    prompt = novelist_prompt or DEFAULT_NOVELIST_PROMPT
    context_lines = []
    if instructions:
        context_lines.append(f"Instructions: {instructions}")
    if language:
        context_lines.append(f"Target language: {language}")
    if answers:
        context_lines.append("Clarifications:")
        context_lines.extend(f"- {a}" for a in answers)
    context_block = "\n".join(context_lines)
    user_prompt = (
        f"{context_block}\n\nSource block:\n{chunk}\n\nWrite the novelized text now."
    )
    return await _llm_chat(
        client, model=model, system=prompt, user=user_prompt, temperature=0.9
    )


async def _merge_chunks(
    client: OpenAIClient,
    *,
    drafts: list[str],
    instructions: str | None,
    language: str | None,
    model: str,
) -> str:
    system_prompt = (
        "You merge multiple scene fragments into one coherent narrative while keeping voice consistent."
    )
    pieces = "\n\n---\n\n".join(drafts)
    user_prompt = (
        f"Language: {language or 'match input'}\nInstructions: {instructions or 'None'}\n"
        f"Merge the following fragments into one continuous narrative with smooth transitions:\n{pieces}"
    )
    return await _llm_chat(client, model=model, system=system_prompt, user=user_prompt)


async def _critic_pass(
    client: OpenAIClient,
    *,
    draft: str,
    critic_prompt: str | None,
    model: str,
) -> str:
    prompt = critic_prompt or DEFAULT_CRITIC_PROMPT
    return await _llm_chat(client, model=model, system=prompt, user=draft, temperature=0.3)


async def _apply_critic(
    client: OpenAIClient,
    *,
    draft: str,
    critic_notes: str,
    instructions: str | None,
    language: str | None,
    model: str,
) -> str:
    system_prompt = "Apply the requested fixes faithfully while keeping style consistent."
    user_prompt = (
        f"Language: {language or 'match input'}\nInstructions: {instructions or 'None'}\n"
        f"Original draft:\n{draft}\n\nCritic notes:\n{critic_notes}\n\nApply changes and return the improved text."
    )
    return await _llm_chat(client, model=model, system=system_prompt, user=user_prompt)


async def _execute_run(
    *,
    run_id: str,
    request_payload: dict[str, Any],
    job_id: int,
) -> dict[str, Any]:
    async with AsyncSessionMaker() as session:
        repo = NovelistRepository(session)
        agent_repo = AgentRepository(session)

        run = await repo.get_run(run_id)
        if not run:
            raise ValueError("Novelist run not found")
        agent = await agent_repo.get_by_id(run.agent_id)
        if not agent:
            raise ValueError("Agent not found")

        llm_client = OpenAIClient(
            api_key=settings.openai_api_key,
            timeout=90,
            max_retries=2,
        )

        try:
            await update_job_progress(job_id, 0.05, {"status": "loading sources"})
            # Load sources
            sources = request_payload.get("sources") or []
            language = request_payload.get("language")
            instructions = request_payload.get("instructions")
            chunk_size = int(request_payload.get("chunk_size") or 2000)
            max_chunks = int(request_payload.get("max_chunks") or 4)
            q_per_chunk = int(request_payload.get("questions_per_chunk") or 10)
            novelist_prompt = request_payload.get("novelist_prompt")
            critic_prompt = request_payload.get("critic_prompt")

            texts: list[tuple[str, str | None]] = []
            for src in sources:
                try:
                    content = _load_source(src)
                except Exception as exc:
                    logger.error("Failed to load source %s: %s", src, exc)
                    content = ""
                label = src.get("label")
                texts.append((content, label))

            all_chunks: list[dict[str, Any]] = []
            for text, label in texts:
                for idx, chunk in enumerate(
                    _chunk_words(text, chunk_size=chunk_size, max_chunks=max_chunks)
                ):
                    all_chunks.append(
                        {
                            "index": len(all_chunks),
                            "source_label": label,
                            "raw_preview": chunk[:400],
                            "status": "pending",
                            "questions": [],
                            "answers": [],
                            "draft": None,
                        }
                    )

            if not all_chunks:
                raise ValueError("No content to process")

            await repo.update_status(
                run_id,
                stage=NovelistStage.QUESTIONS,
                chunks=all_chunks,
                status=NovelistRunStatus.RUNNING,
            )
            await session.commit()
            await update_job_progress(job_id, 0.15, {"status": "generating questions"})

            semaphore = asyncio.Semaphore(4)

            async def process_questions(chunk_entry: dict[str, Any]) -> None:
                async with semaphore:
                    questions = await _generate_questions(
                        llm_client,
                        chunk=chunk_entry["raw_preview"] or "",
                        instructions=instructions,
                        questions_per_chunk=q_per_chunk,
                        model=settings.model_validation,
                    )
                    chunk_entry["questions"] = questions
                    chunk_entry["status"] = "questions"

            await asyncio.gather(*(process_questions(c) for c in all_chunks))
            await repo.update_status(run_id, chunks=all_chunks)
            await session.commit()
            await update_job_progress(job_id, 0.3, {"status": "answering questions"})

            async def process_answers(chunk_entry: dict[str, Any]) -> None:
                async with semaphore:
                    answers = await _answer_questions(
                        llm_client,
                        chunk=chunk_entry["raw_preview"] or "",
                        questions=chunk_entry.get("questions") or [],
                        instructions=instructions,
                        model=settings.model_subanswer,
                    )
                    chunk_entry["answers"] = answers
                    chunk_entry["status"] = "answered"

            await asyncio.gather(*(process_answers(c) for c in all_chunks))
            await repo.update_status(run_id, chunks=all_chunks, stage=NovelistStage.DRAFTING)
            await session.commit()
            await update_job_progress(job_id, 0.5, {"status": "drafting chunks"})

            async def draft_chunk(chunk_entry: dict[str, Any]) -> None:
                async with semaphore:
                    draft = await _novelize_chunk(
                        llm_client,
                        chunk=chunk_entry["raw_preview"] or "",
                        instructions=instructions,
                        language=language,
                        novelist_prompt=novelist_prompt,
                        answers=chunk_entry.get("answers") or [],
                        model=settings.model_novelist_draft,
                    )
                    chunk_entry["draft"] = draft
                    chunk_entry["status"] = "drafted"

            await asyncio.gather(*(draft_chunk(c) for c in all_chunks))
            await repo.update_status(run_id, chunks=all_chunks, stage=NovelistStage.MERGING)
            await session.commit()
            await update_job_progress(job_id, 0.65, {"status": "merging narrative"})

            drafts = [c["draft"] for c in all_chunks if c.get("draft")]
            merged = await _merge_chunks(
                llm_client,
                drafts=drafts,
                instructions=instructions,
                language=language,
                model=settings.model_novelist_draft,
            )

            await update_job_progress(job_id, 0.75, {"status": "critic review"})
            critic_notes = await _critic_pass(
                llm_client,
                draft=merged,
                critic_prompt=critic_prompt,
                model=settings.model_novelist_critic,
            )
            await repo.update_status(
                run_id, chunks=all_chunks, draft_text=merged, critic_notes=critic_notes, stage=NovelistStage.CRITIC
            )
            await session.commit()

            await update_job_progress(job_id, 0.9, {"status": "applying critic notes"})
            improved = await _apply_critic(
                llm_client,
                draft=merged,
                critic_notes=critic_notes,
                instructions=instructions,
                language=language,
                model=settings.model_novelist_draft,
            )

            await repo.update_status(
                run_id,
                status=NovelistRunStatus.COMPLETED,
                stage=NovelistStage.DONE,
                draft_text=improved,
                chunks=all_chunks,
                critic_notes=critic_notes,
            )
            await session.commit()

            await update_job_progress(job_id, 1.0, {"status": "completed"})
            await mark_job_done(job_id, {"run_id": run_id, "status": "completed"})
            return {"run_id": run_id, "draft_text": improved}
        except Exception as exc:
            logger.error("Novelist run %s failed: %s", run_id, exc, exc_info=True)
            await repo.update_status(
                run_id,
                status=NovelistRunStatus.FAILED,
                error_message=str(exc),
            )
            await session.commit()
            await mark_job_failed(job_id, str(exc))
            raise
        finally:
            await llm_client.aclose()


@celery_app.task(name="novelist.generate_draft")
def generate_draft(
    run_id: str,
    request_payload: dict[str, Any],
    *,
    author_type: str = "user",
    author_id: str = "system",
) -> dict[str, Any]:
    """Entry-point Celery task for novelist draft generation (step 1)."""
    description = f"Novelist draft generation for run {run_id}"
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.NOVELIST_DRAFT,
            description=description,
            celery_task_id=generate_draft.request.id,
            details={"run_id": run_id},
        )
    )

    try:
        run_async(mark_job_running(job_id))
        # Attach job to run
        async def _attach() -> None:
            async with AsyncSessionMaker() as session:
                repo = NovelistRepository(session)
                await repo.attach_background_job(run_id, job_id)
                await session.commit()

        run_async(_attach())
        result = run_async(
            _execute_run(run_id=run_id, request_payload=request_payload, job_id=job_id)
        )
        return {"job_id": job_id, "status": "success", "run_id": run_id, **result}
    except Exception as exc:
        run_async(mark_job_failed(job_id, str(exc)))
        raise
