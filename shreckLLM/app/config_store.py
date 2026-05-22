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
MIGRATION_MODELS_V1_KEY = "migration_provider_models_v1_applied"
MIGRATION_OPENAI_MODELS_V2_KEY = "migration_openai_models_v2_applied"

_cache: "RuntimeConfig | None" = None
_lock = threading.Lock()


class ProviderDefaults(BaseModel):
    default_model: str
    models: list[str] = Field(default_factory=list)
    base_url: str | None = None
    api_key: str | None = None


class RuntimeConfig(BaseModel):
    default_provider_id: str = "ollama"
    provider_defaults: dict[str, ProviderDefaults] = Field(default_factory=dict)
    memory_ttl_seconds: int = 3600
    memory_max_messages: int = 24
    max_concurrent_requests: int = 8
    request_timeout_seconds: float = 180.0
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
        raw_models = raw.get("models")
        models: list[str] = []
        if isinstance(raw_models, list):
            for item in raw_models:
                if isinstance(item, str):
                    cleaned = item.strip()
                    if cleaned and cleaned not in models:
                        models.append(cleaned)
        if default_model not in models:
            models.insert(0, default_model)

        provider_defaults[provider_id] = ProviderDefaults(
            default_model=default_model,
            models=models,
            base_url=raw.get("base_url") if isinstance(raw.get("base_url"), str) or raw.get("base_url") is None else None,
            api_key=raw.get("api_key") if isinstance(raw.get("api_key"), str) or raw.get("api_key") is None else None,
        )

    default_provider_id = next(iter(provider_defaults.keys()), "ollama")

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
        migration_applied = bool(current.get(MIGRATION_MODELS_V1_KEY))
        if not migration_applied:
            runtime = RuntimeConfig(**merged)
            migrated_providers: dict[str, ProviderDefaults] = dict(runtime.provider_defaults)
            changed = False

            for provider_id, bootstrap_defaults in defaults.get("provider_defaults", {}).items():
                if not isinstance(bootstrap_defaults, dict):
                    continue
                incoming_default = str(bootstrap_defaults.get("default_model") or "").strip()
                incoming_models = bootstrap_defaults.get("models")
                incoming_models_list = (
                    [m for m in incoming_models if isinstance(m, str) and m.strip()]
                    if isinstance(incoming_models, list)
                    else []
                )
                cfg = migrated_providers.get(provider_id)
                if cfg is None:
                    if incoming_default:
                        migrated_providers[provider_id] = ProviderDefaults(
                            default_model=incoming_default,
                            models=incoming_models_list or [incoming_default],
                            base_url=bootstrap_defaults.get("base_url"),
                            api_key=bootstrap_defaults.get("api_key"),
                        )
                        changed = True
                    continue

                updated_models = list(cfg.models)
                if not updated_models and cfg.default_model:
                    updated_models.append(cfg.default_model)
                for model in incoming_models_list:
                    model_clean = model.strip()
                    if model_clean and model_clean not in updated_models:
                        updated_models.append(model_clean)
                if cfg.default_model and cfg.default_model not in updated_models:
                    updated_models.insert(0, cfg.default_model)
                if updated_models != cfg.models:
                    migrated_providers[provider_id] = ProviderDefaults(
                        default_model=cfg.default_model,
                        models=updated_models,
                        base_url=cfg.base_url,
                        api_key=cfg.api_key,
                    )
                    changed = True

            updated_payload = RuntimeConfig(
                default_provider_id=runtime.default_provider_id,
                provider_defaults=migrated_providers,
                memory_ttl_seconds=runtime.memory_ttl_seconds,
                memory_max_messages=runtime.memory_max_messages,
                max_concurrent_requests=runtime.max_concurrent_requests,
                request_timeout_seconds=runtime.request_timeout_seconds,
                max_queue_wait_seconds=runtime.max_queue_wait_seconds,
            ).model_dump()

            ts = _now()
            if changed:
                conn.executemany(
                    f"""
                    INSERT INTO {CONFIG_TABLE} (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                      value=excluded.value,
                      updated_at=excluded.updated_at
                    """,
                    [(k, _serialize(v), ts) for k, v in updated_payload.items()],
                )
            conn.execute(
                f"""
                INSERT INTO {CONFIG_TABLE} (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value=excluded.value,
                  updated_at=excluded.updated_at
                """,
                (MIGRATION_MODELS_V1_KEY, _serialize(True), ts),
            )
            conn.commit()
            merged = {**updated_payload, MIGRATION_MODELS_V1_KEY: True}

        openai_migration_applied = bool(current.get(MIGRATION_OPENAI_MODELS_V2_KEY))
        if not openai_migration_applied:
            runtime = RuntimeConfig(**merged)
            providers = dict(runtime.provider_defaults)
            openai_cfg = providers.get("openai")
            changed = False

            allowed_openai_models = ["gpt-5", "gpt-5-nano", "gpt-4o-mini"]
            if openai_cfg is not None:
                filtered_models = [m for m in openai_cfg.models if m in allowed_openai_models]
                if not filtered_models:
                    filtered_models = list(allowed_openai_models)

                default_model = openai_cfg.default_model
                if default_model not in filtered_models:
                    default_model = "gpt-5-nano" if "gpt-5-nano" in filtered_models else filtered_models[0]

                if filtered_models != openai_cfg.models or default_model != openai_cfg.default_model:
                    providers["openai"] = ProviderDefaults(
                        default_model=default_model,
                        models=filtered_models,
                        base_url=openai_cfg.base_url,
                        api_key=openai_cfg.api_key,
                    )
                    changed = True

            updated_payload = RuntimeConfig(
                default_provider_id=runtime.default_provider_id,
                provider_defaults=providers,
                memory_ttl_seconds=runtime.memory_ttl_seconds,
                memory_max_messages=runtime.memory_max_messages,
                max_concurrent_requests=runtime.max_concurrent_requests,
                request_timeout_seconds=runtime.request_timeout_seconds,
                max_queue_wait_seconds=runtime.max_queue_wait_seconds,
            ).model_dump()

            ts = _now()
            if changed:
                conn.executemany(
                    f"""
                    INSERT INTO {CONFIG_TABLE} (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                      value=excluded.value,
                      updated_at=excluded.updated_at
                    """,
                    [(k, _serialize(v), ts) for k, v in updated_payload.items()],
                )
            conn.execute(
                f"""
                INSERT INTO {CONFIG_TABLE} (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value=excluded.value,
                  updated_at=excluded.updated_at
                """,
                (MIGRATION_OPENAI_MODELS_V2_KEY, _serialize(True), ts),
            )
            conn.commit()
            merged = {**updated_payload, MIGRATION_OPENAI_MODELS_V2_KEY: True}
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
