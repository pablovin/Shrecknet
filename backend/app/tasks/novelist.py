"""Celery task for Novelist draft generation (step 1)."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.celery_app import celery_app
from app.core.config_store import get_settings, is_openai_configured
from app.db.session import AsyncSessionMaker
from app.integrations.llm.model_policy import ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
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

        llm_client = OpenAIClient(
            api_key=settings.openai_api_key,
            timeout=180,
            max_retries=3,
        )
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
            orchestrator = NovelistOrchestrator(
                llm_client=llm_client,
                model_policy=model_policy,
                max_concurrency=2,
            )

            await repo.update_status(
                run_id,
                status=NovelistRunStatus.RUNNING,
                stage=NovelistStage.INGEST,
            )
            await session.commit()

            await update_job_progress(job_id, 0.1, {"status": "preparing sources"})

            stage_progress = {
                NovelistStage.PLANNING: (0.25, "Planning chapter parts"),
                NovelistStage.WRITING: (0.55, "Drafting chapter parts"),
                NovelistStage.CRITIC: (0.75, "Critic review"),
                NovelistStage.APPLY_CRITIC: (0.9, "Applying critic revisions"),
                NovelistStage.MERGING: (0.95, "Merging final chapter"),
            }

            async def stage_callback(
                stage: NovelistStage, payload: dict[str, Any] | None = None
            ) -> None:
                payload = payload or {}
                update_kwargs: dict[str, Any] = {"stage": stage}
                if "artifacts" in payload:
                    update_kwargs["artifacts"] = payload["artifacts"]
                if "draft_text" in payload:
                    update_kwargs["draft_text"] = payload["draft_text"]
                if "critic_notes" in payload:
                    critic_notes = payload["critic_notes"]
                    if critic_notes is None:
                        update_kwargs["critic_notes"] = None
                    elif isinstance(critic_notes, str):
                        update_kwargs["critic_notes"] = critic_notes
                    else:
                        update_kwargs["critic_notes"] = json.dumps(
                            critic_notes, ensure_ascii=True
                        )
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
                stage_callback=stage_callback,
            )
            await repo.update_status(
                run_id,
                status=NovelistRunStatus.COMPLETED,
                stage=NovelistStage.DONE,
                artifacts=result.get("artifacts"),
                draft_text=result.get("draft_text"),
                critic_notes=result.get("critic_notes"),
            )
            await session.commit()
            await update_job_progress(job_id, 1.0, {"status": "completed"})
            await mark_job_done(job_id, {"run_id": run_id, "status": "completed"})
            return result
        except Exception as exc:
            logger.error("Novelist run %s failed: %s", run_id, exc, exc_info=True)
            await session.rollback()
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
