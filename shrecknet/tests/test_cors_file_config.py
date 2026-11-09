import sqlite3

import pytest
from fastapi import HTTPException
from app.core import config_store
from app.api.routers import configurations
from shreckconfig import CORS_FIELDS


def test_cors_not_exposed_as_runtime_configuration():
    schema = configurations.get_config_schema()
    assert not CORS_FIELDS.intersection(config_store.Settings.model_fields)
    assert not CORS_FIELDS.intersection(schema["field_meta"])
    with pytest.raises(HTTPException) as error:
        configurations._validate_updates({"cors_allow_origins": ["https://untrusted.example"]})
    assert error.value.status_code == 400
    with pytest.raises(ValueError, match="shared .env"):
        config_store.update_settings({"cors_allow_origins": []})


def test_database_cors_is_removed_and_not_reseeded(tmp_path, monkeypatch):
    path = tmp_path / "config.db"
    monkeypatch.setattr(config_store, "_connect", lambda: sqlite3.connect(path))
    with sqlite3.connect(path) as conn:
        config_store._ensure_schema(conn)
        conn.execute(f"INSERT INTO {config_store.CONFIG_TABLE} (key, value, updated_at) VALUES (?, ?, ?)",
                     ("cors_allow_origins", '["https://stale.example"]', "now"))
    monkeypatch.setenv("SHRECKNET_CORS_ALLOW_ORIGINS", "invalid-json")
    config_store.load_settings()
    with sqlite3.connect(path) as conn:
        assert not CORS_FIELDS.intersection(config_store._load_settings_from_db(conn))


def test_media_regex_uses_full_origin_matching():
    from app.main import _origin_matches
    pattern = r"https://table\.example"
    assert _origin_matches("https://table.example", [], pattern)
    assert not _origin_matches("https://table.example.attacker.test", [], pattern)


def test_example_policy_allows_companion_admission_preflight():
    from pathlib import Path
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.testclient import TestClient
    from shreckconfig import load_cors_policy

    policy = load_cors_policy(Path(__file__).resolve().parents[2] / "shrecknet.env.example")
    app = FastAPI()
    app.add_middleware(CORSMiddleware, **policy.model_dump())
    client = TestClient(app)
    response = client.options("/users/me/character-companion/chats", headers={
        "Origin": "https://foundry.shrecknet.club",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://foundry.shrecknet.club"
    assert "idempotency-key" in response.headers["access-control-allow-headers"].lower()
    denied = client.options("/users/me/character-companion/chats", headers={
        "Origin": "https://untrusted.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
    })
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers
