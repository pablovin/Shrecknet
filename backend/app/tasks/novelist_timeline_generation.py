from __future__ import annotations

import logging
from typing import Any

from app.celery_app import celery_app
from app.core.config_store import get_settings, is_openai_configured
from app.graph.neo4j import get_driver
from app.integrations.llm.model_policy import ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.jobs.architect.timeline_generation import generate_timeline_events_for_entity
from app.models.background_job import AuthorType, JobType
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)

logger = logging.getLogger(__name__)


@celery_app.task(name="novelist.generate_timeline_for_entity")
def generate_timeline_for_entity(
    *,
    agent_id: str,
    entity_instance_id: str,
    max_events: int = 3,
    force: bool = False,
    author_type: str = "user",
    author_id: str = "system",
) -> dict[str, Any]:
    description = f"Novelist timeline generation for entity {entity_instance_id}"
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.NOVELIST_TIMELINE_GENERATION,
            description=description,
            celery_task_id=generate_timeline_for_entity.request.id,
            details={
                "agent_id": agent_id,
                "entity_instance_id": entity_instance_id,
                "max_events": max_events,
                "force": force,
                "owner": "novelist",
            },
        )
    )

    try:
        run_async(mark_job_running(job_id))
        run_async(
            update_job_progress(
                job_id, 0.05, {"status": "Preparing timeline generation"}
            )
        )
        result = run_async(
            _execute_timeline_generation(
                entity_instance_id=entity_instance_id,
                max_events=max_events,
                force=force,
                job_id=job_id,
            )
        )

        if result.get("status") == "skipped":
            run_async(mark_job_done(job_id, {"status": "skipped", **result}))
            return {"job_id": job_id, **result}

        run_async(mark_job_done(job_id, {"status": "completed", **result}))
        return {"job_id": job_id, **result}
    except Exception as exc:  # pragma: no cover
        logger.error(
            "novelist_timeline_generation failed for entity %s: %s",
            entity_instance_id,
            exc,
            exc_info=True,
        )
        run_async(mark_job_failed(job_id, str(exc)))
        raise


async def _execute_timeline_generation(
    *,
    entity_instance_id: str,
    max_events: int,
    force: bool,
    job_id: int,
) -> dict[str, Any]:
    settings = get_settings()
    if not is_openai_configured(settings):
        raise RuntimeError("OpenAI API key not configured")

    driver = get_driver()
    async with driver.session(database=settings.neo4j_database) as graph_session:
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
        llm_client = OpenAIClient(
            api_key=settings.openai_api_key,
            timeout=60,
            max_retries=3,
        )
        try:
            await update_job_progress(
                job_id, 0.25, {"status": "Extracting timeline events"}
            )
            result = await generate_timeline_events_for_entity(
                graph_session=graph_session,
                llm_client=llm_client,
                model_policy=model_policy,
                entity_instance_id=entity_instance_id,
                max_events=max_events,
                force=force,
            )
        finally:
            await llm_client.aclose()

    await update_job_progress(
        job_id,
        0.95,
        {
            "status": "Completed" if result.get("status") == "completed" else "Skipped",
            "created_event_count": len(result.get("created_event_ids", [])),
        },
    )
    return result
