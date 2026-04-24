from __future__ import annotations

import base64
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import redis

from app.core.config_store import get_settings

logger = logging.getLogger(__name__)


def _sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    raw = url.removeprefix("sqlite:///")
    if raw.startswith("./"):
        return (Path.cwd() / raw[2:]).resolve()
    return Path(raw)


def _open_sqlite(url: str) -> sqlite3.Connection | None:
    path = _sqlite_path_from_url(url)
    if path is None or not path.exists():
        return None
    conn = sqlite3.connect(path.as_posix())
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_table_exists(conn: sqlite3.Connection | None, table_name: str) -> bool:
    if conn is None:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _decode_kwargs(message: dict[str, Any]) -> dict[str, Any]:
    body_b64 = message.get("body")
    if not isinstance(body_b64, str):
        return {}
    try:
        body_raw = base64.b64decode(body_b64)
        decoded = json.loads(body_raw.decode("utf-8"))
    except Exception:
        return {}
    if (
        isinstance(decoded, list)
        and len(decoded) >= 2
        and isinstance(decoded[1], dict)
    ):
        return decoded[1]
    return {}


def _is_terminal_run_status(conn: sqlite3.Connection | None, table: str, run_id: str) -> bool:
    if conn is None:
        return False
    try:
        row = conn.execute(f"SELECT status FROM {table} WHERE id = ?", (run_id,)).fetchone()
    except sqlite3.Error:
        return False
    if row is None:
        return True
    status = str(row["status"] or "").lower()
    return status in {"completed", "failed"}


def _instance_exists(conn: sqlite3.Connection | None, instance_id: str) -> bool:
    if conn is None:
        return True
    try:
        row = conn.execute(
            "SELECT 1 FROM ontology_instances WHERE instance_id = ? LIMIT 1",
            (instance_id,),
        ).fetchone()
    except sqlite3.Error:
        return True
    return row is not None


def reap_stale_celery_messages() -> dict[str, int]:
    settings = get_settings()
    redis_client = redis.Redis.from_url(settings.celery_broker_url, decode_responses=True)
    max_age = max(60, int(settings.celery_stale_reaper_max_task_age_seconds))
    now = int(time.time())
    queues = ("ontology_linking", "architect")

    app_conn = _open_sqlite(settings.database_url)
    removed = 0
    inspected = 0
    failed_parse = 0

    try:
        for queue in queues:
            entries = redis_client.lrange(queue, 0, -1)
            for raw_entry in entries:
                inspected += 1
                try:
                    message = json.loads(raw_entry)
                except Exception:
                    failed_parse += 1
                    continue

                headers = message.get("headers") or {}
                task_name = str(headers.get("task") or "")
                kwargs = _decode_kwargs(message)

                stale = False
                enqueued_at = headers.get("x-enqueued-at")
                if isinstance(enqueued_at, int):
                    stale = (now - enqueued_at) > max_age
                elif isinstance(enqueued_at, str) and enqueued_at.isdigit():
                    stale = (now - int(enqueued_at)) > max_age

                if not stale and task_name in {"architect.analyze_instance", "architect.generate_entities"}:
                    run_id = str(kwargs.get("run_id") or "")
                    if run_id:
                        stale = _is_terminal_run_status(app_conn, "architect_analysis_runs", run_id)

                if not stale and task_name == "novelist.generate_draft":
                    run_id = str(kwargs.get("run_id") or "")
                    if run_id:
                        stale = _is_terminal_run_status(app_conn, "novelist_runs", run_id)

                if not stale and task_name in {"ontology.embed_reconciliation", "ontology.link_instance"}:
                    instance_id = kwargs.get("instance_id")
                    if isinstance(instance_id, str) and instance_id.strip():
                        stale = not _instance_exists(app_conn, instance_id.strip())

                if stale:
                    removed += int(redis_client.lrem(queue, 1, raw_entry) or 0)
    finally:
        if app_conn is not None:
            app_conn.close()

    stats = {"inspected": inspected, "removed": removed, "failed_parse": failed_parse}
    if removed > 0:
        logger.info("Celery stale queue reaper removed=%s inspected=%s", removed, inspected)
    return stats


def fail_non_terminal_background_jobs(reason: str | None = None) -> int:
    settings = get_settings()
    jobs_conn = _open_sqlite(settings.jobs_database_url)
    if jobs_conn is None or not _sqlite_table_exists(jobs_conn, "background_jobs"):
        if jobs_conn is not None:
            jobs_conn.close()
        return 0

    failure_reason = (
        reason
        or "Job failed during startup cleanup because a previous worker/session ended unexpectedly."
    )
    completed_at = datetime.now(timezone.utc).isoformat()
    try:
        cursor = jobs_conn.execute(
            """
            UPDATE background_jobs
            SET
                status = 'failed',
                error_message = CASE
                    WHEN error_message IS NULL OR TRIM(error_message) = ''
                    THEN ?
                    ELSE error_message
                END,
                completed_at = COALESCE(completed_at, ?)
            WHERE status IN ('queued', 'running')
            """,
            (failure_reason, completed_at),
        )
        jobs_conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        jobs_conn.close()


def purge_celery_queues(queue_names: tuple[str, ...] | None = None) -> dict[str, int]:
    settings = get_settings()
    redis_client = redis.Redis.from_url(settings.celery_broker_url, decode_responses=True)
    queues = queue_names or ("ontology_linking", "architect")

    purged = 0
    inspected = 0
    for queue in queues:
        try:
            inspected += int(redis_client.llen(queue) or 0)
            purged += int(redis_client.delete(queue) or 0)
        except Exception:
            logger.exception("Failed purging Celery queue '%s'", queue)
    return {"inspected": inspected, "purged_keys": purged}


def reset_jobs_and_queues_on_startup() -> dict[str, int]:
    failed_jobs = fail_non_terminal_background_jobs()
    purge_stats = purge_celery_queues()
    stats = {
        "failed_jobs": failed_jobs,
        "queued_messages_inspected": purge_stats["inspected"],
        "queues_purged": purge_stats["purged_keys"],
    }
    if failed_jobs > 0 or purge_stats["inspected"] > 0:
        logger.warning("Startup Celery cleanup applied: %s", stats)
    else:
        logger.info("Startup Celery cleanup found no stale jobs/messages")
    return stats
