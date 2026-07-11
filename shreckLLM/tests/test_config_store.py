from __future__ import annotations

import json
import sqlite3

import app.config_store as config_store
from app.config import Settings
from app.config_store import CONFIG_TABLE
from app.config_store import EXTERNAL_OLLAMA_BASE_URL
from app.config_store import LEGACY_COMPOSE_OLLAMA_BASE_URL
from app.config_store import MIGRATION_BOOTSTRAP_PROVIDERS_V3_KEY
from app.config_store import MIGRATION_MODELS_V1_KEY
from app.config_store import MIGRATION_OLLAMA_CLOUD_MODELS_V4_KEY
from app.config_store import MIGRATION_OPENAI_MODELS_V2_KEY
from app.config_store import MIGRATION_PROVIDER_ACTIVE_STATE_V7_KEY
from app.config_store import MIGRATION_REMOVE_DEFAULT_PROVIDER_MODEL_V6_KEY
from app.config_store import ProviderDefaults
from app.config_store import RuntimeConfig


def _patch_settings(monkeypatch, tmp_path, **kwargs):
    settings = Settings(data_dir=str(tmp_path), **kwargs)
    config_store._cache = None
    monkeypatch.setattr(config_store, "get_settings", lambda: settings)
    return settings


def _seed_runtime_config(tmp_path, runtime: RuntimeConfig) -> None:
    db_path = tmp_path / "shreckllm_config.db"
    with sqlite3.connect(db_path.as_posix()) as conn:
        config_store._ensure_schema(conn)
        rows = [(key, json.dumps(value), "2026-01-01T00:00:00+00:00") for key, value in runtime.model_dump().items()]
        rows.extend(
            [
                (MIGRATION_MODELS_V1_KEY, json.dumps(True), "2026-01-01T00:00:00+00:00"),
                (MIGRATION_OPENAI_MODELS_V2_KEY, json.dumps(True), "2026-01-01T00:00:00+00:00"),
                (MIGRATION_BOOTSTRAP_PROVIDERS_V3_KEY, json.dumps(True), "2026-01-01T00:00:00+00:00"),
                (MIGRATION_OLLAMA_CLOUD_MODELS_V4_KEY, json.dumps(True), "2026-01-01T00:00:00+00:00"),
            ]
        )
        conn.executemany(
            f"INSERT INTO {CONFIG_TABLE} (key, value, updated_at) VALUES (?, ?, ?)",
            rows,
        )


def _seed_runtime_config_rows(tmp_path, rows: dict[str, object]) -> None:
    db_path = tmp_path / "shreckllm_config.db"
    with sqlite3.connect(db_path.as_posix()) as conn:
        config_store._ensure_schema(conn)
        conn.executemany(
            f"INSERT INTO {CONFIG_TABLE} (key, value, updated_at) VALUES (?, ?, ?)",
            [(key, json.dumps(value), "2026-01-01T00:00:00+00:00") for key, value in rows.items()],
        )


def _load_runtime_config_rows(tmp_path) -> dict[str, object]:
    db_path = tmp_path / "shreckllm_config.db"
    with sqlite3.connect(db_path.as_posix()) as conn:
        return {
            key: json.loads(value)
            for key, value in conn.execute(f"SELECT key, value FROM {CONFIG_TABLE}").fetchall()
        }


def test_fresh_bootstrap_uses_external_host_ollama_url(monkeypatch, tmp_path) -> None:
    _patch_settings(monkeypatch, tmp_path)

    runtime = config_store.load_runtime_config()

    assert runtime.provider_defaults["ollama"].base_url == EXTERNAL_OLLAMA_BASE_URL


def test_legacy_compose_ollama_url_migrates_to_external_host(monkeypatch, tmp_path) -> None:
    _patch_settings(monkeypatch, tmp_path)
    _seed_runtime_config(
        tmp_path,
        RuntimeConfig(
            provider_defaults={
                "ollama": ProviderDefaults(
                    kind="local",
                    auth_strategy="none",
                    healthcheck_path="/api/tags",
                    models=["custom-model", "another-model"],
                    base_url=LEGACY_COMPOSE_OLLAMA_BASE_URL,
                    api_key="local-secret",
                )
            }
        ),
    )

    runtime = config_store.load_runtime_config()
    ollama = runtime.provider_defaults["ollama"]

    assert ollama.base_url == EXTERNAL_OLLAMA_BASE_URL
    assert ollama.models == ["custom-model", "another-model"]
    assert ollama.api_key is None
    assert ollama.provider_type == "needs_baseurl"
    assert ollama.website_url == "https://ollama.com/download"
    assert ollama.kind == "local"
    assert ollama.auth_strategy == "none"


def test_custom_ollama_url_is_not_overwritten(monkeypatch, tmp_path) -> None:
    custom_url = "http://ollama.example.internal:11434"
    _patch_settings(monkeypatch, tmp_path)
    _seed_runtime_config(
        tmp_path,
        RuntimeConfig(
            provider_defaults={
                "ollama": ProviderDefaults(
                    models=["custom-model"],
                    base_url=custom_url,
                )
            }
        ),
    )

    runtime = config_store.load_runtime_config()

    assert runtime.provider_defaults["ollama"].base_url == custom_url


def test_remove_default_provider_model_migration_cleans_persisted_rows(monkeypatch, tmp_path) -> None:
    _patch_settings(monkeypatch, tmp_path)
    _seed_runtime_config_rows(
        tmp_path,
        {
            "default_provider_id": "ollama",
            "provider_defaults": {
                "ollama": {
                    "kind": "local",
                    "auth_strategy": "none",
                    "healthcheck_path": "/api/tags",
                    "default_model": "legacy-model",
                    "models": ["new-model"],
                    "base_url": LEGACY_COMPOSE_OLLAMA_BASE_URL,
                    "api_key": None,
                }
            },
            "provider_states": {},
            MIGRATION_MODELS_V1_KEY: True,
            MIGRATION_OPENAI_MODELS_V2_KEY: True,
            MIGRATION_BOOTSTRAP_PROVIDERS_V3_KEY: True,
            MIGRATION_OLLAMA_CLOUD_MODELS_V4_KEY: True,
        },
    )

    runtime = config_store.load_runtime_config()
    rows = _load_runtime_config_rows(tmp_path)

    assert runtime.provider_defaults["ollama"].models == ["legacy-model", "new-model"]
    assert "default_provider_id" not in rows
    assert "default_model" not in rows["provider_defaults"]["ollama"]
    assert rows["provider_defaults"]["ollama"]["models"] == ["legacy-model", "new-model"]
    assert rows[MIGRATION_REMOVE_DEFAULT_PROVIDER_MODEL_V6_KEY] is True


def test_remove_default_provider_model_migration_does_not_duplicate_model(monkeypatch, tmp_path) -> None:
    _patch_settings(monkeypatch, tmp_path)
    _seed_runtime_config_rows(
        tmp_path,
        {
            "provider_defaults": {
                "openai": {
                    "kind": "cloud",
                    "auth_strategy": "api_key",
                    "default_model": "gpt-5-nano",
                    "models": ["gpt-5-nano", "gpt-5"],
                    "base_url": None,
                    "api_key": "",
                }
            },
            "provider_states": {},
            MIGRATION_MODELS_V1_KEY: True,
            MIGRATION_OPENAI_MODELS_V2_KEY: True,
            MIGRATION_BOOTSTRAP_PROVIDERS_V3_KEY: True,
            MIGRATION_OLLAMA_CLOUD_MODELS_V4_KEY: True,
        },
    )

    runtime = config_store.load_runtime_config()
    rows = _load_runtime_config_rows(tmp_path)

    assert runtime.provider_defaults["openai"].models == ["gpt-5-nano", "gpt-5"]
    assert rows["provider_defaults"]["openai"]["models"] == ["gpt-5-nano", "gpt-5"]
    assert "default_model" not in rows["provider_defaults"]["openai"]
    assert rows[MIGRATION_REMOVE_DEFAULT_PROVIDER_MODEL_V6_KEY] is True


def test_provider_active_state_migration_maps_legacy_valid_to_active(monkeypatch, tmp_path) -> None:
    _patch_settings(monkeypatch, tmp_path)
    _seed_runtime_config_rows(
        tmp_path,
        {
            "provider_defaults": {
                "openai": {
                    "kind": "cloud",
                    "auth_strategy": "api_key",
                    "models": ["gpt-5-nano"],
                    "base_url": None,
                    "api_key": "",
                }
            },
            "provider_states": {
                "openai": {
                    "active": False,
                    "valid": True,
                    "last_validated_at": "2026-01-01T00:00:00+00:00",
                }
            },
            MIGRATION_MODELS_V1_KEY: True,
            MIGRATION_OPENAI_MODELS_V2_KEY: True,
            MIGRATION_BOOTSTRAP_PROVIDERS_V3_KEY: True,
            MIGRATION_OLLAMA_CLOUD_MODELS_V4_KEY: True,
            MIGRATION_REMOVE_DEFAULT_PROVIDER_MODEL_V6_KEY: True,
        },
    )

    runtime = config_store.load_runtime_config()
    rows = _load_runtime_config_rows(tmp_path)

    assert runtime.provider_states["openai"].active is True
    assert rows["provider_states"]["openai"]["active"] is True
    assert "valid" not in rows["provider_states"]["openai"]
    assert rows[MIGRATION_PROVIDER_ACTIVE_STATE_V7_KEY] is True
