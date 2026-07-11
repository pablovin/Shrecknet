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
MIGRATION_BOOTSTRAP_PROVIDERS_V3_KEY = "migration_bootstrap_providers_v3_applied"
MIGRATION_OLLAMA_CLOUD_MODELS_V4_KEY = "migration_ollama_cloud_models_v4_applied"
MIGRATION_EXTERNAL_OLLAMA_URL_V5_KEY = "migration_external_ollama_url_v5_applied"
MIGRATION_REMOVE_DEFAULT_PROVIDER_MODEL_V6_KEY = "migration_remove_default_provider_model_v6_applied"
MIGRATION_PROVIDER_ACTIVE_STATE_V7_KEY = "migration_provider_active_state_v7_applied"
MIGRATION_PROVIDER_METADATA_V8_KEY = "migration_provider_metadata_v8_applied"
LEGACY_COMPOSE_OLLAMA_BASE_URL = "http://ollama:11434"
EXTERNAL_OLLAMA_BASE_URL = "http://host.docker.internal:11434"

# These capabilities and help links are product metadata, rather than values an
# administrator should be able to override at runtime.
PROVIDER_METADATA: dict[str, dict[str, str]] = {
    "openai": {
        "provider_type": "needs_api",
        "website_url": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "provider_type": "needs_api",
        "website_url": "https://console.anthropic.com/settings/keys",
    },
    "ollama_cloud": {
        "provider_type": "needs_api",
        "website_url": "https://ollama.com/settings/keys",
    },
    "ollama": {
        "provider_type": "needs_baseurl",
        "website_url": "https://ollama.com/download",
    },
}

_cache: "RuntimeConfig | None" = None
_lock = threading.Lock()


class ProviderDefaults(BaseModel):
    kind: str = "cloud"
    auth_strategy: str = "none"
    healthcheck_path: str | None = None
    models: list[str] = Field(default_factory=list)
    base_url: str | None = None
    api_key: str | None = None
    # UI capability metadata.  This is configuration-owned, not user-editable.
    provider_type: str | None = None
    website_url: str | None = None


class ProviderState(BaseModel):
    active: bool = False
    last_validated_at: str | None = None
    last_validation_checked_at: str | None = None
    last_validation_failed_at: str | None = None
    last_validation_error: str | None = None
    last_warmed_at: str | None = None
    last_error: str | None = None


class RuntimeConfig(BaseModel):
    provider_defaults: dict[str, ProviderDefaults] = Field(default_factory=dict)
    provider_states: dict[str, ProviderState] = Field(default_factory=dict)
    memory_ttl_seconds: int = 3600
    memory_max_messages: int = 24
    max_concurrent_requests: int = 8
    request_timeout_seconds: float = 180.0
    max_queue_wait_seconds: float = 10.0
    provider_limits: dict[str, dict[str, float | int]] = Field(default_factory=dict)
    chat_job_queue_max_size: int = 256
    chat_job_result_ttl_seconds: int = 900
    chat_job_poll_default_interval_ms: int = 250
    chat_job_max_retries: int = 2


class RuntimeConfigUpdate(BaseModel):
    provider_defaults: dict[str, ProviderDefaults] | None = None
    provider_states: dict[str, ProviderState] | None = None
    memory_ttl_seconds: int | None = None
    memory_max_messages: int | None = None
    max_concurrent_requests: int | None = None
    request_timeout_seconds: float | None = None
    max_queue_wait_seconds: float | None = None
    provider_limits: dict[str, dict[str, float | int]] | None = None
    chat_job_queue_max_size: int | None = None
    chat_job_result_ttl_seconds: int | None = None
    chat_job_poll_default_interval_ms: int | None = None
    chat_job_max_retries: int | None = None


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
    provider_states: dict[str, ProviderState] = {}
    for provider_id, raw in settings.bootstrap_provider_defaults.items():
        if not isinstance(raw, dict):
            continue
        raw_models = raw.get("models")
        models: list[str] = []
        if isinstance(raw_models, list):
            for item in raw_models:
                if isinstance(item, str):
                    cleaned = item.strip()
                    if cleaned and cleaned not in models:
                        models.append(cleaned)
        legacy_default_model = str(raw.get("default_model") or "").strip()
        if legacy_default_model and legacy_default_model not in models:
            models.insert(0, legacy_default_model)
        if not models:
            continue

        provider_defaults[provider_id] = ProviderDefaults(
            kind=str(raw.get("kind") or "").strip() or ("local" if provider_id == "ollama" else "cloud"),
            auth_strategy=str(raw.get("auth_strategy") or "").strip() or ("none" if provider_id == "ollama" else "api_key"),
            healthcheck_path=raw.get("healthcheck_path") if isinstance(raw.get("healthcheck_path"), str) or raw.get("healthcheck_path") is None else None,
            models=models,
            base_url=raw.get("base_url") if isinstance(raw.get("base_url"), str) or raw.get("base_url") is None else None,
            api_key=raw.get("api_key") if isinstance(raw.get("api_key"), str) or raw.get("api_key") is None else None,
            provider_type=raw.get("provider_type") if isinstance(raw.get("provider_type"), str) else None,
            website_url=raw.get("website_url") if isinstance(raw.get("website_url"), str) else None,
        )
        provider_states[provider_id] = ProviderState(active=False)

    return RuntimeConfig(
        provider_defaults=provider_defaults,
        provider_states=provider_states,
        memory_ttl_seconds=settings.bootstrap_memory_ttl_seconds,
        memory_max_messages=settings.bootstrap_memory_max_messages,
        max_concurrent_requests=settings.bootstrap_max_concurrent_requests,
        request_timeout_seconds=settings.bootstrap_request_timeout_seconds,
        max_queue_wait_seconds=settings.bootstrap_max_queue_wait_seconds,
        provider_limits=getattr(settings, "bootstrap_provider_limits", {}) or {},
        chat_job_queue_max_size=getattr(settings, "bootstrap_chat_job_queue_max_size", 256),
        chat_job_result_ttl_seconds=getattr(settings, "bootstrap_chat_job_result_ttl_seconds", 900),
        chat_job_poll_default_interval_ms=getattr(settings, "bootstrap_chat_job_poll_default_interval_ms", 250),
        chat_job_max_retries=getattr(settings, "bootstrap_chat_job_max_retries", 2),
    )


def _load_from_db(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(f"SELECT key, value FROM {CONFIG_TABLE}").fetchall()
    return {key: _deserialize(value) for key, value in rows}


def _normalize_provider_defaults_payload(payload: dict[str, Any]) -> dict[str, Any]:
    providers = payload.get("provider_defaults")
    if not isinstance(providers, dict):
        return payload
    normalized: dict[str, Any] = {}
    for provider_id, raw in providers.items():
        if not isinstance(raw, dict):
            normalized[provider_id] = raw
            continue
        row = dict(raw)
        models = row.get("models")
        clean_models = [str(model).strip() for model in models if isinstance(model, str) and model.strip()] if isinstance(models, list) else []
        legacy_default = str(row.pop("default_model", "") or "").strip()
        if legacy_default and legacy_default not in clean_models:
            clean_models.insert(0, legacy_default)
        row["models"] = clean_models
        normalized[provider_id] = row
    return {**payload, "provider_defaults": normalized}


def _remove_legacy_default_provider_model(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    cleaned = dict(payload)
    changed = False

    if "default_provider_id" in cleaned:
        cleaned.pop("default_provider_id", None)
        changed = True

    providers = cleaned.get("provider_defaults")
    if not isinstance(providers, dict):
        return cleaned, changed

    normalized: dict[str, Any] = {}
    for provider_id, raw in providers.items():
        if not isinstance(raw, dict):
            normalized[provider_id] = raw
            continue

        row = dict(raw)
        models = row.get("models")
        clean_models = [str(model).strip() for model in models if isinstance(model, str) and model.strip()] if isinstance(models, list) else []
        legacy_default = str(row.pop("default_model", "") or "").strip()
        if "default_model" in raw:
            changed = True
        if legacy_default and legacy_default not in clean_models:
            clean_models.insert(0, legacy_default)
            changed = True
        if clean_models != models:
            changed = True
        row["models"] = clean_models
        normalized[provider_id] = row

    if normalized != providers:
        changed = True
    cleaned["provider_defaults"] = normalized
    return cleaned, changed


def _has_legacy_default_provider_model(payload: dict[str, Any]) -> bool:
    if "default_provider_id" in payload:
        return True
    providers = payload.get("provider_defaults")
    if not isinstance(providers, dict):
        return False
    return any(isinstance(raw, dict) and "default_model" in raw for raw in providers.values())


def _normalize_provider_active_state(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    states = payload.get("provider_states")
    if not isinstance(states, dict):
        return payload, False

    changed = False
    normalized: dict[str, Any] = {}
    for provider_id, raw in states.items():
        if not isinstance(raw, dict):
            normalized[provider_id] = raw
            continue
        row = dict(raw)
        legacy_valid = row.pop("valid", None)
        if "valid" in raw:
            changed = True
        if legacy_valid is True and row.get("active") is not True:
            row["active"] = True
            changed = True
        normalized[provider_id] = row

    if normalized != states:
        changed = True
    return {**payload, "provider_states": normalized}, changed


def _apply_provider_metadata(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    providers = payload.get("provider_defaults")
    if not isinstance(providers, dict):
        return payload, False
    normalized: dict[str, Any] = {}
    changed = False
    for provider_id, raw in providers.items():
        if not isinstance(raw, dict):
            normalized[provider_id] = raw
            continue
        row = dict(raw)
        metadata = PROVIDER_METADATA.get(provider_id)
        if metadata:
            for key, value in metadata.items():
                if row.get(key) != value:
                    row[key] = value
                    changed = True
            # Ollama is deliberately unauthenticated; remove any old stored key.
            if provider_id == "ollama" and row.get("api_key") is not None:
                row["api_key"] = None
                changed = True
        normalized[provider_id] = row
    return {**payload, "provider_defaults": normalized}, changed


def _has_legacy_provider_valid_state(payload: dict[str, Any]) -> bool:
    states = payload.get("provider_states")
    if not isinstance(states, dict):
        return False
    return any(isinstance(raw, dict) and "valid" in raw for raw in states.values())


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

        merged = _normalize_provider_defaults_payload({**defaults, **current})
        migration_applied = bool(current.get(MIGRATION_MODELS_V1_KEY))
        if not migration_applied:
            runtime = RuntimeConfig(**merged)
            migrated_providers: dict[str, ProviderDefaults] = dict(runtime.provider_defaults)
            changed = False

            for provider_id, bootstrap_defaults in defaults.get("provider_defaults", {}).items():
                if not isinstance(bootstrap_defaults, dict):
                    continue
                incoming_models = bootstrap_defaults.get("models")
                incoming_models_list = (
                    [m for m in incoming_models if isinstance(m, str) and m.strip()]
                    if isinstance(incoming_models, list)
                    else []
                )
                cfg = migrated_providers.get(provider_id)
                if cfg is None:
                    legacy_default = str(bootstrap_defaults.get("default_model") or "").strip()
                    if legacy_default and legacy_default not in incoming_models_list:
                        incoming_models_list.insert(0, legacy_default)
                    if incoming_models_list:
                        migrated_providers[provider_id] = ProviderDefaults(
                            kind=str(bootstrap_defaults.get("kind") or "").strip() or ("local" if provider_id == "ollama" else "cloud"),
                            auth_strategy=str(bootstrap_defaults.get("auth_strategy") or "").strip() or ("none" if provider_id == "ollama" else "api_key"),
                            healthcheck_path=bootstrap_defaults.get("healthcheck_path") if isinstance(bootstrap_defaults.get("healthcheck_path"), str) or bootstrap_defaults.get("healthcheck_path") is None else None,
                            models=incoming_models_list,
                            base_url=bootstrap_defaults.get("base_url"),
                            api_key=bootstrap_defaults.get("api_key"),
                        )
                        changed = True
                    continue

                updated_models = list(cfg.models)
                for model in incoming_models_list:
                    model_clean = model.strip()
                    if model_clean and model_clean not in updated_models:
                        updated_models.append(model_clean)
                if updated_models != cfg.models:
                    migrated_providers[provider_id] = ProviderDefaults(
                        kind=cfg.kind,
                        auth_strategy=cfg.auth_strategy,
                        healthcheck_path=cfg.healthcheck_path,
                        models=updated_models,
                        base_url=cfg.base_url,
                        api_key=cfg.api_key,
                    )
                    changed = True

            updated_payload = RuntimeConfig(
                provider_defaults=migrated_providers,
                provider_states=runtime.provider_states,
                memory_ttl_seconds=runtime.memory_ttl_seconds,
                memory_max_messages=runtime.memory_max_messages,
                max_concurrent_requests=runtime.max_concurrent_requests,
                request_timeout_seconds=runtime.request_timeout_seconds,
                max_queue_wait_seconds=runtime.max_queue_wait_seconds,
                provider_limits=runtime.provider_limits,
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

                if filtered_models != openai_cfg.models:
                    providers["openai"] = ProviderDefaults(
                        kind=openai_cfg.kind,
                        auth_strategy=openai_cfg.auth_strategy,
                        healthcheck_path=openai_cfg.healthcheck_path,
                        models=filtered_models,
                        base_url=openai_cfg.base_url,
                        api_key=openai_cfg.api_key,
                    )
                    changed = True

            updated_payload = RuntimeConfig(
                provider_defaults=providers,
                provider_states=runtime.provider_states,
                memory_ttl_seconds=runtime.memory_ttl_seconds,
                memory_max_messages=runtime.memory_max_messages,
                max_concurrent_requests=runtime.max_concurrent_requests,
                request_timeout_seconds=runtime.request_timeout_seconds,
                max_queue_wait_seconds=runtime.max_queue_wait_seconds,
                provider_limits=runtime.provider_limits,
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

        bootstrap_providers_migration_applied = bool(current.get(MIGRATION_BOOTSTRAP_PROVIDERS_V3_KEY))
        if not bootstrap_providers_migration_applied:
            runtime = RuntimeConfig(**merged)
            providers = dict(runtime.provider_defaults)
            changed = False

            for provider_id, bootstrap_defaults in defaults.get("provider_defaults", {}).items():
                if provider_id in providers or not isinstance(bootstrap_defaults, dict):
                    continue
                incoming_models = bootstrap_defaults.get("models")
                incoming_models_list = (
                    [m for m in incoming_models if isinstance(m, str) and m.strip()]
                    if isinstance(incoming_models, list)
                    else []
                )
                legacy_default = str(bootstrap_defaults.get("default_model") or "").strip()
                if legacy_default and legacy_default not in incoming_models_list:
                    incoming_models_list.insert(0, legacy_default)
                if not incoming_models_list:
                    continue
                providers[provider_id] = ProviderDefaults(
                    kind=str(bootstrap_defaults.get("kind") or "").strip() or ("local" if provider_id == "ollama" else "cloud"),
                    auth_strategy=str(bootstrap_defaults.get("auth_strategy") or "").strip() or ("none" if provider_id == "ollama" else "api_key"),
                    healthcheck_path=bootstrap_defaults.get("healthcheck_path") if isinstance(bootstrap_defaults.get("healthcheck_path"), str) or bootstrap_defaults.get("healthcheck_path") is None else None,
                    models=incoming_models_list,
                    base_url=bootstrap_defaults.get("base_url"),
                    api_key=bootstrap_defaults.get("api_key"),
                )
                changed = True

            updated_payload = RuntimeConfig(
                provider_defaults=providers,
                provider_states=runtime.provider_states,
                memory_ttl_seconds=runtime.memory_ttl_seconds,
                memory_max_messages=runtime.memory_max_messages,
                max_concurrent_requests=runtime.max_concurrent_requests,
                request_timeout_seconds=runtime.request_timeout_seconds,
                max_queue_wait_seconds=runtime.max_queue_wait_seconds,
                provider_limits=runtime.provider_limits,
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
                (MIGRATION_BOOTSTRAP_PROVIDERS_V3_KEY, _serialize(True), ts),
            )
            conn.commit()
            merged = {**updated_payload, MIGRATION_BOOTSTRAP_PROVIDERS_V3_KEY: True}

        ollama_cloud_models_migration_applied = bool(current.get(MIGRATION_OLLAMA_CLOUD_MODELS_V4_KEY))
        if not ollama_cloud_models_migration_applied:
            runtime = RuntimeConfig(**merged)
            providers = dict(runtime.provider_defaults)
            changed = False

            cfg = providers.get("ollama_cloud")
            if cfg is not None:
                required_models = ["gemma4:31b", "gemma4:31b-cloud"]
                merged_models: list[str] = []
                for model in [*cfg.models, *required_models]:
                    cleaned = model.strip() if isinstance(model, str) else ""
                    if cleaned and cleaned not in merged_models:
                        merged_models.append(cleaned)
                if merged_models != cfg.models:
                    providers["ollama_cloud"] = ProviderDefaults(
                        kind=cfg.kind,
                        auth_strategy=cfg.auth_strategy,
                        healthcheck_path=cfg.healthcheck_path,
                        models=merged_models,
                        base_url=cfg.base_url,
                        api_key=cfg.api_key,
                    )
                    changed = True

            updated_payload = RuntimeConfig(
                provider_defaults=providers,
                provider_states=runtime.provider_states,
                memory_ttl_seconds=runtime.memory_ttl_seconds,
                memory_max_messages=runtime.memory_max_messages,
                max_concurrent_requests=runtime.max_concurrent_requests,
                request_timeout_seconds=runtime.request_timeout_seconds,
                max_queue_wait_seconds=runtime.max_queue_wait_seconds,
                provider_limits=runtime.provider_limits,
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
                (MIGRATION_OLLAMA_CLOUD_MODELS_V4_KEY, _serialize(True), ts),
            )
            conn.commit()
            merged = {**updated_payload, MIGRATION_OLLAMA_CLOUD_MODELS_V4_KEY: True}

        external_ollama_url_migration_applied = bool(current.get(MIGRATION_EXTERNAL_OLLAMA_URL_V5_KEY))
        if not external_ollama_url_migration_applied:
            runtime = RuntimeConfig(**merged)
            providers = dict(runtime.provider_defaults)
            changed = False

            cfg = providers.get("ollama")
            if cfg is not None and (cfg.base_url or "").rstrip("/") == LEGACY_COMPOSE_OLLAMA_BASE_URL:
                providers["ollama"] = ProviderDefaults(
                    kind=cfg.kind,
                    auth_strategy=cfg.auth_strategy,
                    healthcheck_path=cfg.healthcheck_path,
                    models=cfg.models,
                    base_url=EXTERNAL_OLLAMA_BASE_URL,
                    api_key=cfg.api_key,
                )
                changed = True

            updated_payload = RuntimeConfig(
                provider_defaults=providers,
                provider_states=runtime.provider_states,
                memory_ttl_seconds=runtime.memory_ttl_seconds,
                memory_max_messages=runtime.memory_max_messages,
                max_concurrent_requests=runtime.max_concurrent_requests,
                request_timeout_seconds=runtime.request_timeout_seconds,
                max_queue_wait_seconds=runtime.max_queue_wait_seconds,
                provider_limits=runtime.provider_limits,
                chat_job_queue_max_size=runtime.chat_job_queue_max_size,
                chat_job_result_ttl_seconds=runtime.chat_job_result_ttl_seconds,
                chat_job_poll_default_interval_ms=runtime.chat_job_poll_default_interval_ms,
                chat_job_max_retries=runtime.chat_job_max_retries,
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
                (MIGRATION_EXTERNAL_OLLAMA_URL_V5_KEY, _serialize(True), ts),
            )
            conn.commit()
            merged = {**updated_payload, MIGRATION_EXTERNAL_OLLAMA_URL_V5_KEY: True}

        remove_default_migration_applied = bool(current.get(MIGRATION_REMOVE_DEFAULT_PROVIDER_MODEL_V6_KEY))
        if not remove_default_migration_applied:
            cleaned_merged, changed = _remove_legacy_default_provider_model(merged)
            legacy_persisted = _has_legacy_default_provider_model(current)
            runtime = RuntimeConfig(**cleaned_merged)
            updated_payload = runtime.model_dump()

            ts = _now()
            if changed or legacy_persisted:
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
            conn.execute(f"DELETE FROM {CONFIG_TABLE} WHERE key = ?", ("default_provider_id",))
            conn.execute(
                f"""
                INSERT INTO {CONFIG_TABLE} (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value=excluded.value,
                  updated_at=excluded.updated_at
                """,
                (MIGRATION_REMOVE_DEFAULT_PROVIDER_MODEL_V6_KEY, _serialize(True), ts),
            )
            conn.commit()
            merged = {**updated_payload, MIGRATION_REMOVE_DEFAULT_PROVIDER_MODEL_V6_KEY: True}

        provider_active_migration_applied = bool(current.get(MIGRATION_PROVIDER_ACTIVE_STATE_V7_KEY))
        if not provider_active_migration_applied:
            legacy_persisted = _has_legacy_provider_valid_state(current)
            source_payload = merged
            if legacy_persisted and isinstance(current.get("provider_states"), dict):
                source_payload = {**merged, "provider_states": current["provider_states"]}
            cleaned_merged, changed = _normalize_provider_active_state(source_payload)
            runtime = RuntimeConfig(**cleaned_merged)
            updated_payload = runtime.model_dump()

            ts = _now()
            if changed or legacy_persisted:
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
                (MIGRATION_PROVIDER_ACTIVE_STATE_V7_KEY, _serialize(True), ts),
            )
            conn.commit()
            merged = {**updated_payload, MIGRATION_PROVIDER_ACTIVE_STATE_V7_KEY: True}

        provider_metadata_migration_applied = bool(current.get(MIGRATION_PROVIDER_METADATA_V8_KEY))
        if not provider_metadata_migration_applied:
            enriched_merged, changed = _apply_provider_metadata(merged)
            runtime = RuntimeConfig(**enriched_merged)
            updated_payload = runtime.model_dump()
            ts = _now()
            if changed:
                conn.executemany(
                    f"""
                    INSERT INTO {CONFIG_TABLE} (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    [(k, _serialize(v), ts) for k, v in updated_payload.items()],
                )
            conn.execute(
                f"""
                INSERT INTO {CONFIG_TABLE} (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (MIGRATION_PROVIDER_METADATA_V8_KEY, _serialize(True), ts),
            )
            conn.commit()
            merged = {**updated_payload, MIGRATION_PROVIDER_METADATA_V8_KEY: True}
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
