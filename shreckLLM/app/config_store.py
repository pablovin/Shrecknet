from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.config import Settings, get_settings

CONFIG_TABLE = "config_settings"

_cache: "RuntimeConfig | None" = None
_lock = threading.Lock()


class ProviderDefaults(BaseModel):
    default_model: str
    base_url: str | None = None
    api_key: str | None = None


class RuntimeConfig(BaseModel):
    default_provider_id: str = "ollama"
    provider_defaults: dict[str, ProviderDefaults] = Field(default_factory=dict)
    memory_ttl_seconds: int = 3600
    memory_max_messages: int = 24
    max_concurrent_requests: int = 8
    request_timeout_seconds: float = 45.0
    max_queue_wait_seconds: float = 10.0


class RuntimeConfigUpdate(BaseModel):
    default_provider_id: str | None = None
    provider_defaults: dict[str, ProviderDefaults] | None = None
    memory_ttl_seconds: int | None = None
    memory_max_messages: int | None = None
    max_concurrent_requests: int | None = None
    request_timeout_seconds: float | None = None
    max_queue_wait_seconds: float | None = None


def _db_path() -> Path:
    settings = get_settings()
    path = Path(settings.data_dir) / "shreckllm_config.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(_db_path().as_posix())


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CONFIG_TABLE} (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _deserialize(raw: str) -> Any:
    return json.loads(raw)


def _bootstrap_defaults(settings: Settings) -> RuntimeConfig:
    provider_defaults: dict[str, ProviderDefaults] = {}
    for provider_id, raw in settings.bootstrap_provider_defaults.items():
        if not isinstance(raw, dict):
            continue
        default_model = str(raw.get("default_model") or "").strip()
        if not default_model:
            continue
        provider_defaults[provider_id] = ProviderDefaults(
            default_model=default_model,
            base_url=raw.get("base_url") if isinstance(raw.get("base_url"), str) or raw.get("base_url") is None else None,
            api_key=raw.get("api_key") if isinstance(raw.get("api_key"), str) or raw.get("api_key") is None else None,
        )

    if settings.bootstrap_default_provider_id not in provider_defaults and provider_defaults:
        default_provider_id = next(iter(provider_defaults.keys()))
    else:
        default_provider_id = settings.bootstrap_default_provider_id

    return RuntimeConfig(
        default_provider_id=default_provider_id,
        provider_defaults=provider_defaults,
        memory_ttl_seconds=settings.bootstrap_memory_ttl_seconds,
        memory_max_messages=settings.bootstrap_memory_max_messages,
        max_concurrent_requests=settings.bootstrap_max_concurrent_requests,
        request_timeout_seconds=settings.bootstrap_request_timeout_seconds,
        max_queue_wait_seconds=settings.bootstrap_max_queue_wait_seconds,
    )


def _load_from_db(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(f"SELECT key, value FROM {CONFIG_TABLE}").fetchall()
    return {key: _deserialize(value) for key, value in rows}


def load_runtime_config() -> RuntimeConfig:
    settings = get_settings()
    defaults = _bootstrap_defaults(settings).model_dump()

    conn = _connect()
    try:
        _ensure_schema(conn)
        current = _load_from_db(conn)
        missing = {k: v for k, v in defaults.items() if k not in current}
        if missing:
            ts = _now()
            conn.executemany(
                f"INSERT INTO {CONFIG_TABLE} (key, value, updated_at) VALUES (?, ?, ?)",
                [(k, _serialize(v), ts) for k, v in missing.items()],
            )
            conn.commit()
        merged = {**defaults, **current}
    finally:
        conn.close()

    return RuntimeConfig(**merged)


def get_runtime_config() -> RuntimeConfig:
    global _cache
    with _lock:
        if _cache is None:
            _cache = load_runtime_config()
        return _cache


def reload_runtime_config() -> RuntimeConfig:
    global _cache
    with _lock:
        _cache = load_runtime_config()
        return _cache


def update_runtime_config(patch: dict[str, Any]) -> RuntimeConfig:
    global _cache
    with _lock:
        current = _cache or load_runtime_config()
        current_data = current.model_dump()
        current_data.update(patch)
        updated = RuntimeConfig(**current_data).model_dump()

        conn = _connect()
        try:
            _ensure_schema(conn)
            ts = _now()
            conn.executemany(
                f"""
                INSERT INTO {CONFIG_TABLE} (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value=excluded.value,
                  updated_at=excluded.updated_at
                """,
                [(k, _serialize(v), ts) for k, v in updated.items()],
            )
            conn.commit()
        finally:
            conn.close()

        _cache = RuntimeConfig(**updated)
        return _cache
