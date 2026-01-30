"""Celery task for Novelist draft generation (step 1)."""

from __future__ import annotations

import logging
from typing import Any

from app.celery_app import celery_app
from app.core.config_store import get_settings, is_openai_configured
from app.db.session import AsyncSessionMaker
from app.graph.neo4j import get_driver
from app.integrations.llm.model_policy import ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.integrations.retrieval.neo4j_retriever import Neo4jGraphRetriever
from app.jobs.elder.elder import ElderOrchestrator
from app.jobs.novelist.novelist import NovelistOrchestrator
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

logger = logging.getLogger(__name__)


async def _execute_run(
    *,
    run_id: str,
    request_payload: dict[str, Any],
    job_id: int,
) -> dict[str, Any]:
    settings = get_settings()
    if not is_openai_configured(settings):
        raise RuntimeError("OpenAI API key not configured")
    async with AsyncSessionMaker() as session:
        repo = NovelistRepository(session)
        agent_repo = AgentRepository(session)

        run = await repo.get_run(run_id)
        if not run:
            raise ValueError("Novelist run not found")

        novelist_agent = await agent_repo.get_by_id(run.agent_id)
        if not novelist_agent:
            raise ValueError("Agent not found")

        elder_agent_id = request_payload.get("elder_agent_id")
        elder_agent = None
        if elder_agent_id:
            elder_agent = await agent_repo.get_by_id(elder_agent_id)
            if elder_agent and elder_agent.job != "elder":
                elder_agent = None

        llm_client = OpenAIClient(
            api_key=settings.openai_api_key,
            timeout=90,
            max_retries=2,
        )
        driver = get_driver()
        graph_session = driver.session(database=settings.neo4j_database)

        try:
            await update_job_progress(job_id, 0.05, {"status": "preparing orchestrator"})
            model_policy = ModelPolicy(
                decompose_model=settings.model_decompose,
                subanswer_model=settings.model_subanswer,
                synthesis_model=settings.model_synthesis,
                validation_model=settings.model_validation,
                style_model=settings.model_style,
                architect_extract_model=getattr(
                    settings, "model_architect_extract", settings.model_decompose
                ),
            )
            # Attach novelist-specific model preferences for orchestrator
            setattr(model_policy, "model_novelist_draft", settings.model_novelist_draft)
            setattr(model_policy, "model_novelist_critic", settings.model_novelist_critic)
            elder_orchestrator = None
            if elder_agent:
                elder_orchestrator = ElderOrchestrator(
                    llm_client=llm_client,
                    model_policy=model_policy,
                    graph_retriever=Neo4jGraphRetriever(graph_session),
                    default_top_k=settings.default_top_k,
                )

            orchestrator = NovelistOrchestrator(
                llm_client=llm_client,
                model_policy=model_policy,
                graph_retriever=Neo4jGraphRetriever(graph_session),
                elder_orchestrator=elder_orchestrator,
                default_chunk_size=request_payload.get("chunk_size") or 2000,
                default_max_chunks=request_payload.get("max_chunks") or 4,
                default_questions_per_chunk=request_payload.get("questions_per_chunk") or 10,
            )

            await repo.update_status(
                run_id,
                status=NovelistRunStatus.RUNNING,
                stage=NovelistStage.INGEST,
            )
            await session.commit()

            await update_job_progress(job_id, 0.1, {"status": "preparing sources"})

            stage_progress = {
                NovelistStage.QUESTIONS: (0.25, "Generating clarifying questions"),
                NovelistStage.ANSWERS: (0.4, "Gathering elder answers"),
                NovelistStage.DRAFTING: (0.6, "Drafting chunk narratives"),
                NovelistStage.MERGING: (0.72, "Merging narrative"),
                NovelistStage.CRITIC: (0.85, "Critic review"),
            }

            async def stage_callback(
                stage: NovelistStage, payload: dict[str, Any] | None = None
            ) -> None:
                payload = payload or {}
                update_kwargs: dict[str, Any] = {"stage": stage}
                if "chunks" in payload:
                    update_kwargs["chunks"] = payload["chunks"]
                if "draft_text" in payload:
                    update_kwargs["draft_text"] = payload["draft_text"]
                if "critic_notes" in payload:
                    update_kwargs["critic_notes"] = payload["critic_notes"]
                await repo.update_status(run_id, **update_kwargs)
                await session.commit()
                progress_info = stage_progress.get(stage)
                if progress_info:
                    progress_value, progress_status = progress_info
                    await update_job_progress(
                        job_id, progress_value, {"status": progress_status}
                    )

            result = await orchestrator.execute(
                agent=novelist_agent,
                payload=NovelistRunCreate.model_validate(request_payload),
                elder_agent=elder_agent,
                stage_callback=stage_callback,
            )

            await update_job_progress(job_id, 0.95, {"status": "finalizing"})
            await repo.update_status(
                run_id,
                status=NovelistRunStatus.COMPLETED,
                stage=NovelistStage.DONE,
                chunks=result.get("chunks"),
                draft_text=result.get("draft_text"),
                critic_notes=result.get("critic_notes"),
            )
            await session.commit()
            await update_job_progress(job_id, 1.0, {"status": "completed"})
            await mark_job_done(job_id, {"run_id": run_id, "status": "completed"})
            return result
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
            await graph_session.close()


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
