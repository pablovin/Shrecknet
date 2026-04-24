from __future__ import annotations

import base64
import json
import logging
import sqlite3
import time
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

