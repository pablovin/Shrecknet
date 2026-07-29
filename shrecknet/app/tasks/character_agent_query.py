"""Celery entry point for asynchronous CharacterAgent queries."""

from __future__ import annotations

from typing import Any

from app.celery_app import celery_app
from app.core.config_store import get_settings
from app.graph.neo4j import get_driver
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.character_agent.query import (
    CharacterAgentQueryJob,
    CharacterGenerationError,
)
from app.schemas.character_agent import CharacterAgentQueryRequest
from app.services.character_agent_service import CharacterAgentService
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)


def _details(
    *,
    agent_id: str,
    stage: str,
    result: dict[str, Any] | None = None,
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "character_agent_id": agent_id,
        "stage": stage,
        "result": result,
        "error": error,
    }


@celery_app.task(name="character_agent.query")
def run_character_agent_query(
    *,
    job_id: int,
    agent_id: str,
    request_payload: dict[str, Any],
    public_only: bool,
) -> dict[str, Any]:
    try:
        run_async(mark_job_running(job_id))
        result = run_async(_run(
            job_id=job_id,
            agent_id=agent_id,
            request_payload=request_payload,
            public_only=public_only,
        ))
        details = _details(
            agent_id=agent_id,
            stage="completed",
            result=result,
        )
        run_async(mark_job_done(job_id, details))
        return details
    except Exception as exc:
        current_stage = getattr(exc, "character_query_stage", "failed")
        error = {
            "code": (
                "invalid_agent_output"
                if isinstance(exc, (ValueError, CharacterGenerationError))
                else "agent_service_unavailable"
            ),
            "message": str(exc) or type(exc).__name__,
        }
        details = _details(
            agent_id=agent_id,
            stage=current_stage,
            error=error,
        )
        run_async(mark_job_failed(job_id, error["message"], details))
        raise


async def _run(
    *,
    job_id: int,
    agent_id: str,
    request_payload: dict[str, Any],
    public_only: bool,
) -> dict[str, Any]:
    request = CharacterAgentQueryRequest.model_validate(request_payload)
    stage = "loading_identity"

    async def report(next_stage: str, progress: float) -> None:
        nonlocal stage
        stage = next_stage
        await update_job_progress(
            job_id,
            progress,
            _details(agent_id=agent_id, stage=next_stage),
        )

    await report(stage, 0.1)
    driver = get_driver()
    try:
        async with driver.session(database=get_settings().neo4j_database) as graph:
            service = CharacterAgentService(None, graph)  # SQL is not used by query reads.
            if request.use_character_identity:
                snapshot = await service.load_query_snapshot(
                    agent_id, public_only=public_only
                )
            else:
                await service.ensure_queryable(agent_id, public_only=public_only)
                snapshot = None
    except Exception as exc:
        setattr(exc, "character_query_stage", stage)
        raise

    settings = get_settings()
    client = ShreckLLMClient(
        base_url=settings.shreckllm_base_url,
        timeout=settings.shreckllm_request_timeout_s,
        max_retries=0,
        poll_without_deadline=True,
    )
    try:
        query = CharacterAgentQueryJob(
            llm_client=client,
            framing_model=settings.model_character_agent_framing,
            deliberation_model=settings.model_character_agent_deliberation,
            repair_model=settings.model_agents_repair_json,
            report_stage=report,
        )
        try:
            result = await query.run(request, snapshot)
        except Exception as exc:
            setattr(exc, "character_query_stage", stage)
            raise
        return result.model_dump(mode="json")
    finally:
        await client.aclose()
