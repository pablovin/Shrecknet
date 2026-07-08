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
                    default_model="custom-model",
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
    assert ollama.default_model == "custom-model"
    assert ollama.models == ["custom-model", "another-model"]
    assert ollama.api_key == "local-secret"
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
                    default_model="custom-model",
                    models=["custom-model"],
                    base_url=custom_url,
                )
            }
        ),
    )

    runtime = config_store.load_runtime_config()

    assert runtime.provider_defaults["ollama"].base_url == custom_url
