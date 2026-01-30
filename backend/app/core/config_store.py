from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CONFIG_DB_FILENAME = "configs.db"
CONFIG_TABLE = "config_settings"

_settings_cache: "Settings | None" = None
_settings_lock = threading.Lock()


def _default_data_dir() -> Path:
    env_value = os.getenv("SHRECKNET_DATA_DIR")
    if env_value:
        data_dir = Path(env_value)
        return data_dir if data_dir.exists() else Path(".")
    if Path("/data").exists():
        return Path("/data")
    data_dir = Path("./databases")
    return data_dir if data_dir.exists() else Path(".")


def _sqlite_url(filename: str) -> str:
    data_dir = _default_data_dir()
    path = data_dir / filename
    if data_dir.is_absolute():
        return f"sqlite+aiosqlite:///{path.as_posix()}"
    return f"sqlite+aiosqlite:///./{path.as_posix()}"


def _env_or_default(name: str, default: str) -> str:
    return os.getenv(name, default)

def _default_media_root() -> str:
    env_value = os.getenv("SHRECKNET_MEDIA_ROOT")
    if env_value:
        return env_value
    if Path("/app/media").exists():
        return "/app/media"
    if Path("backend/media").exists():
        return "backend/media"
    if Path("media").exists():
        return "media"
    return "./media"


class Settings(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    app_name: str = "backend_2"
    database_url: str = Field(default_factory=lambda: _sqlite_url("backend_2.db"))
    jobs_database_url: str = Field(
        default_factory=lambda: _sqlite_url("backend_2_jobs.db")
    )
    debug: bool = False
    secret_key: str = "change-me"
    access_token_expire_minutes: int = 460800
    jwt_algorithm: str = "HS256"
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost",
            "http://localhost:3000",
            "http://localhost:8080",
            "http://127.0.0.1",
            "http://127.0.0.1:8080",
            "https://lovableproject.com",
            "https://shrecknet.club",
            "https://c56f54ad-02b8-428f-9e87-43f81dab0914.lovableproject.com",
        ]
    )
    cors_allow_origin_regex: str | None = r"https://.*\\.lovableproject\\.com"
    cors_allow_credentials: bool = True
    cors_allow_methods: list[str] = Field(default_factory=lambda: ["*"])
    cors_allow_headers: list[str] = Field(default_factory=lambda: ["*"])
    cors_max_age: int = 3600
    media_root: str = Field(default_factory=_default_media_root)
    media_base_url: str = "/media"
    media_public_url: str | None = None
    max_image_upload_bytes: int = 10 * 1024 * 1024
    image_max_width: int = 1024
    image_max_height: int = 1024
    max_pdf_upload_bytes: int = 50 * 1024 * 1024
    library_max_pdf_bytes: int = 500 * 1024 * 1024
    neo4j_uri: str = Field(
        default_factory=lambda: _env_or_default("NEO4J_URI", "bolt://neo4j:7687")
    )
    neo4j_user: str = "neo4j"
    neo4j_password: str = "VeryStrongPass123"
    neo4j_database: str = "neo4j"
    celery_broker_url: str = Field(
        default_factory=lambda: _env_or_default(
            "CELERY_BROKER_URL", "redis://redis:6379/0"
        )
    )
    celery_result_backend: str = Field(
        default_factory=lambda: _env_or_default(
            "CELERY_RESULT_BACKEND", "redis://redis:6379/1"
        )
    )
    celery_task_always_eager: bool = False
    openai_api_key: str = ""
    model_decompose: str = "gpt-4o-mini"
    model_subanswer: str = "gpt-4o-mini"
    model_synthesis: str = "gpt-4o-mini"
    model_validation: str = "gpt-4o-mini"
    model_style: str = "gpt-4o-mini"
    model_architect_extract: str = "gpt-4o-mini"
    model_novelist_draft: str = "gpt-5.1"
    model_novelist_critic: str = "gpt-4o-mini"
    default_top_k: int = 8
    enable_tracing: bool = True
    rate_limit_rpm: int | None = None
    google_service_account_json: str | None = "/app/app/core/shrecknet.json"
    activate_google_calendar: bool = False
    google_calendar_default_duration_minutes: int = 180
    google_delegated_user_email: str | None = "pablovin@shrecknet.club"
    embedding_device: str = "cpu"
    old_database_url: str = "sqlite+aiosqlite:///../backend/data/prod.db"


class AppConfig(BaseModel):
    app_name: str
    debug: bool


def _config_db_path() -> Path:
    return _default_data_dir() / CONFIG_DB_FILENAME


def _connect() -> sqlite3.Connection:
    db_path = _config_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path.as_posix())


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


def _current_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _deserialize_value(raw: str) -> Any:
    return json.loads(raw)


def _default_settings_dict() -> dict[str, Any]:
    return Settings().model_dump()


def _load_settings_from_db(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(f"SELECT key, value FROM {CONFIG_TABLE}").fetchall()
    return {key: _deserialize_value(value) for key, value in rows}


def _seed_defaults_if_needed(conn: sqlite3.Connection) -> dict[str, Any]:
    defaults = _default_settings_dict()
    existing = _load_settings_from_db(conn)
    missing = {key: value for key, value in defaults.items() if key not in existing}
    if missing:
        timestamp = _current_timestamp()
        conn.executemany(
            f"INSERT INTO {CONFIG_TABLE} (key, value, updated_at) VALUES (?, ?, ?)",
            [
                (key, _serialize_value(value), timestamp)
                for key, value in missing.items()
            ],
        )
        conn.commit()
    merged = {**defaults, **existing}
    return merged


def load_settings() -> Settings:
    conn = _connect()
    try:
        _ensure_schema(conn)
        merged = _seed_defaults_if_needed(conn)
    finally:
        conn.close()
    env_media_root = os.getenv("SHRECKNET_MEDIA_ROOT")
    if env_media_root:
        merged["media_root"] = env_media_root
    return Settings(**merged)


def get_settings() -> Settings:
    global _settings_cache
    with _settings_lock:
        if _settings_cache is None:
            _settings_cache = load_settings()
        return _settings_cache


def is_openai_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    raw_key = settings.openai_api_key or ""
    key = raw_key.strip()
    if not key:
        return False
    return key.lower() != "openaikey"


def reload_settings() -> Settings:
    global _settings_cache
    with _settings_lock:
        _settings_cache = load_settings()
        return _settings_cache


def update_settings(updates: dict[str, Any]) -> Settings:
    global _settings_cache
    with _settings_lock:
        current = _settings_cache or load_settings()
        settings = current.model_dump()
        settings.update(updates)
        updated = Settings(**settings).model_dump()
        conn = _connect()
        try:
            _ensure_schema(conn)
            timestamp = _current_timestamp()
            conn.executemany(
                f"""
                INSERT INTO {CONFIG_TABLE} (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                [
                    (key, _serialize_value(value), timestamp)
                    for key, value in updated.items()
                ],
            )
            conn.commit()
        finally:
            conn.close()
        _settings_cache = Settings(**updated)
        return _settings_cache


def get_app_config(settings: Settings | None = None) -> AppConfig:
    settings = settings or get_settings()
    return AppConfig(app_name=settings.app_name, debug=settings.debug)
