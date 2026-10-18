"""Celery entry point for the ShreckWorld-owned evidence agent."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.celery_app import celery_app
from app.db.session import session_factory
from app.jobs.shreckworld_agent.agent import run_shreckworld_agent
from app.models import ShreckWorldAgentJob
from app.schemas.shreckworld import ShreckWorldQueryRequest

logger = logging.getLogger(__name__)


@celery_app.task(name="shreckworld.agent.query")
def run_shreckworld_agent_job(job_id: str) -> None:
    session = session_factory()()
    try:
        job = session.get(ShreckWorldAgentJob, job_id)
        if job is None or job.status != "queued":
            return
        now = datetime.now(timezone.utc)
        job.status, job.stage = "running", "starting"
        job.started_at = job.updated_at = now
        session.commit()
        request = ShreckWorldQueryRequest.model_validate_json(job.request_json)

        def update_stage(stage: str) -> None:
            job.stage = stage
            job.updated_at = datetime.now(timezone.utc)
            session.commit()

        result = run_shreckworld_agent(
            session, shreckworld_id=job.shreckworld_id, request=request, on_stage=update_stage,
        )
        job.status, job.stage = "completed", "completed"
        job.result_json = result.model_dump_json()
        job.error = None
        job.completed_at = job.updated_at = datetime.now(timezone.utc)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("ShreckWorld agent job %s failed", job_id)
        job = session.get(ShreckWorldAgentJob, job_id)
        if job is not None:
            job.status, job.stage = "failed", "failed"
            job.error = "The ShreckWorld agent could not complete this query. Retry the request."
            job.completed_at = job.updated_at = datetime.now(timezone.utc)
            session.commit()
        raise
    finally:
        session.close()
