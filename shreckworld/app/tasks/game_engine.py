"""Persist a Phase 1 placeholder run result; extraction is intentionally deferred."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import update

from app.celery_app import celery_app
from app.db.session import session_factory
from app.models.game_engine import GameGenerationRun

logger = logging.getLogger(__name__)


@celery_app.task(name="shreckworld.game_engine.placeholder")
def run_generation_placeholder(run_id: str) -> None:
    session = session_factory()()
    try:
        now = datetime.now(timezone.utc)
        claimed = session.execute(update(GameGenerationRun).where(
            GameGenerationRun.id == run_id, GameGenerationRun.status == "queued",
        ).values(status="running", started_at=now, updated_at=now))
        if claimed.rowcount != 1:
            return
        session.commit()
        run = session.get(GameGenerationRun, run_id)
        if run is None:
            return
        # This durable task deliberately does no extraction or model call.
        session.refresh(run)
        if run.status == "cancel_requested":
            run.status = "cancelled"
            run.result = None
            run.finished_at = run.updated_at = datetime.now(timezone.utc)
            session.commit()
            return
        if run.status != "running":
            return
        run.status = "succeeded"
        run.result = {"execution_mode": "placeholder", "candidates_created": 0,
                      "extraction_performed": False}
        run.diagnostics = [{"code": "extraction_not_implemented", "retryable": False}]
        run.finished_at = run.updated_at = datetime.now(timezone.utc)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("ShreckWorld game-engine placeholder run %s failed", run_id)
        run = session.get(GameGenerationRun, run_id)
        if run is not None:
            run.status, run.error_code = "failed", "placeholder_run_failed"
            run.finished_at = run.updated_at = datetime.now(timezone.utc)
            session.commit()
        raise
    finally:
        session.close()
