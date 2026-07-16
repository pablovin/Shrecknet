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

                # Instance existence is graph-sourced, not SQL-sourced.
                # Keep messages unless they are stale by age or run-state checks above.

                if stale:
                    removed += int(redis_client.lrem(queue, 1, raw_entry) or 0)
    finally:
        if app_conn is not None:
            app_conn.close()

    stats = {"inspected": inspected, "removed": removed, "failed_parse": failed_parse}
    if removed > 0:
        logger.info("Celery stale queue reaper removed=%s inspected=%s", removed, inspected)
    return stats


def clear_all_background_jobs() -> dict[str, Any]:
    """Delete every persisted job and return Celery task IDs that must be revoked."""
    settings = get_settings()
    jobs_conn = _open_sqlite(settings.jobs_database_url)
    if jobs_conn is None or not _sqlite_table_exists(jobs_conn, "background_jobs"):
        if jobs_conn is not None:
            jobs_conn.close()
        return {"deleted_jobs": 0, "celery_task_ids": []}
    try:
        columns = {
            str(row[1])
            for row in jobs_conn.execute("PRAGMA table_info(background_jobs)").fetchall()
        }
        rows = (
            jobs_conn.execute(
                "SELECT celery_task_id FROM background_jobs WHERE celery_task_id IS NOT NULL"
            ).fetchall()
            if "celery_task_id" in columns
            else []
        )
        cursor = jobs_conn.execute("DELETE FROM background_jobs")
        jobs_conn.commit()
        return {
            "deleted_jobs": int(cursor.rowcount or 0),
            "celery_task_ids": [str(row["celery_task_id"]) for row in rows if row["celery_task_id"]],
        }
    finally:
        jobs_conn.close()


def revoke_celery_tasks(task_ids: list[str]) -> int:
    """Broadcast revocation for known running jobs before purging queued messages."""
    unique_ids = list(dict.fromkeys(task_ids))
    if not unique_ids:
        return 0
    try:
        from app.celery_app import celery_app

        for task_id in unique_ids:
            celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
        return len(unique_ids)
    except Exception:
        logger.exception("Failed revoking Celery tasks during job cleanup")
        return 0


def purge_celery_queues(queue_names: tuple[str, ...] | None = None) -> dict[str, int]:
    settings = get_settings()
    redis_client = redis.Redis.from_url(settings.celery_broker_url, decode_responses=True)
    queues = queue_names or ("ontology_linking", "architect", "celery")

    purged = 0
    inspected = 0
    for queue in queues:
        try:
            inspected += int(redis_client.llen(queue) or 0)
            purged += int(redis_client.delete(queue) or 0)
        except Exception:
            logger.exception("Failed purging Celery queue '%s'", queue)
    # Redis transport keeps reserved messages outside their queue list. Those
    # entries are restored after a worker restart unless they are removed too.
    try:
        stale_keys = {"unacked", "unacked_index"}
        scan_iter = getattr(redis_client, "scan_iter", None)
        if callable(scan_iter):
            stale_keys.update(
                str(key)
                for key in scan_iter(match="*unacked*")
                if "unacked" in str(key)
            )
        for key in stale_keys:
            purged += int(redis_client.delete(key) or 0)
    except Exception:
        logger.exception("Failed purging Celery reserved-message keys")
    return {"inspected": inspected, "purged_keys": purged}


def reset_jobs_and_queues_on_startup() -> dict[str, int]:
    cleared_jobs = clear_all_background_jobs()
    revoked_tasks = revoke_celery_tasks(cleared_jobs["celery_task_ids"])
    purge_stats = purge_celery_queues()
    stats = {
        "jobs_deleted": int(cleared_jobs["deleted_jobs"]),
        "tasks_revoked": revoked_tasks,
        "queued_messages_inspected": purge_stats["inspected"],
        "queues_purged": purge_stats["purged_keys"],
    }
    if stats["jobs_deleted"] > 0 or revoked_tasks > 0 or purge_stats["inspected"] > 0:
        logger.warning("Startup Celery cleanup applied: %s", stats)
    else:
        logger.info("Startup Celery cleanup found no stale jobs/messages")
    return stats


def clear_all_jobs_and_queues() -> dict[str, int]:
    """User-invoked destructive cleanup of job history and all broker queues."""
    return reset_jobs_and_queues_on_startup()
