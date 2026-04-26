from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_FILENAME = "shrecknet.db"
DEFAULT_JOBS_DATABASE_FILENAME = "shrecknet_jobs.db"
CONFIG_DB_FILENAME = "shrecknet_config.db"
LEGACY_CONFIG_DB_FILENAME = "configs.db"
CONFIG_TABLE = "config_settings"
BOOTSTRAP_ENV_FIELDS = frozenset(
    {
        "database_url",
        "jobs_database_url",
        "celery_broker_url",
        "celery_result_backend",
        "jwt_private_key_pem",
        "jwt_public_key_pem",
    }
)

_settings_cache: "Settings | None" = None
_settings_lock = threading.Lock()
_data_dir_cache: Path | None = None
logger = logging.getLogger(__name__)


def _can_write_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    probe = path / ".shrecknet-write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _default_data_dir() -> Path:
    global _data_dir_cache
    if _data_dir_cache is not None:
        return _data_dir_cache

    repo_data_dir = Path(__file__).resolve().parents[2] / "databases"
    env_value = os.getenv("SHRECKNET_DATA_DIR")
    if env_value:
        data_dir = Path(env_value)
        if _can_write_directory(data_dir):
            _data_dir_cache = data_dir
            return _data_dir_cache
        logger.warning(
            "Configured SHRECKNET_DATA_DIR '%s' is not writable; falling back to '%s'.",
            data_dir,
            repo_data_dir,
        )

    docker_data_dir = Path("/data")
    if docker_data_dir.exists() and _can_write_directory(docker_data_dir):
        _data_dir_cache = docker_data_dir
        return _data_dir_cache

    _can_write_directory(repo_data_dir)
    _data_dir_cache = repo_data_dir
    return _data_dir_cache


def _sqlite_url(filename: str) -> str:
    data_dir = _default_data_dir()
    path = data_dir / filename
    if data_dir.is_absolute():
        return f"sqlite:///{path.as_posix()}"
    return f"sqlite:///./{path.as_posix()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHRECKNET_", extra="ignore")

    app_name: str = "shrecknet"
    debug: bool = False
    cors_allow_origins: list[str] = ["http://localhost"]
    cors_allow_origin_regex: str | None = r"https://.*\.lovableproject\.com"
    cors_allow_credentials: bool = True
    cors_allow_methods: list[str] = ["*"]
    cors_allow_headers: list[str] = ["*"]
    cors_max_age: int = 3600
    database_url: str = _sqlite_url(DEFAULT_DATABASE_FILENAME)
    jobs_database_url: str = _sqlite_url(DEFAULT_JOBS_DATABASE_FILENAME)
    media_root: str = "./media"
    media_base_url: str = "/media"
    media_public_url: str | None = None
    max_image_upload_bytes: int = 5 * 1024 * 1024
    image_max_width: int = 4096
    image_max_height: int = 4096
    max_pdf_upload_bytes: int = 30 * 1024 * 1024
    library_max_pdf_bytes: int = 300 * 1024 * 1024
    old_database_url: str = ""

    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "VeryStrongPass123"
    neo4j_database: str = "neo4j"

    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"
    celery_task_always_eager: bool = False
    celery_expires_architect_seconds: int = 3600
    celery_expires_novelist_seconds: int = 3600
    celery_expires_reconciliation_seconds: int = 1800
    celery_stale_reaper_enabled: bool = True
    celery_stale_reaper_interval_seconds: int = 300
    celery_stale_reaper_max_task_age_seconds: int = 7200

    openai_api_key: str = ""
    model_architect_scene_chunking: str = "gpt-5.1"
    model_architect: str = "gpt-5-nano"
    model_elder: str = "gpt-5-nano"
    model_novelist: str = "gpt-5-nano"
    model_novelist_draft: str = "gpt-5.1"
    model_librarian: str = "gpt-5-nano"
    default_top_k: int = 8
    embedding_model_id: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimension: int = 384
    embedding_device: str = "cpu"
    # A value of 1 can introduce queueing under concurrent elder or embedding workloads.
    elder_embedding_inference_concurrency: int = 2
    elder_query_embedding_timeout_s: float = 10.0
    elder_embedding_warmup_on_worker_start: bool = True
    elder_embedding_manager_enabled: bool = True
    elder_embedding_queue_max_size: int = 500
    elder_embedding_batch_max_size: int = 32
    elder_embedding_batch_wait_ms: int = 15
    elder_embedding_cache_size: int = 10_000
    elder_embedding_request_timeout_s: float = 10.0
    embedding_runtime_enabled: bool = True
    embedding_runtime_queue_max_size: int = 500
    embedding_runtime_batch_max_size: int = 32
    embedding_runtime_batch_wait_ms: int = 15
    embedding_runtime_cache_size: int = 10_000
    embedding_runtime_request_timeout_s: float = 10.0
    embedding_runtime_startup_timeout_s: float = 20.0
    embedding_runtime_fail_open_health: bool = True
    embedding_chunk_size: int = 900
    embedding_chunk_overlap: int = 150
    novelist_elder_query_concurrency: int = 1
    novelist_elder_query_timeout_s: int = 75
    event_publisher_mode: str = "logging"
    event_webhook_url: str | None = None

    jwt_issuer: str = "shrecknet"
    jwt_audience: str = "shreckrpg"
    jwt_kid: str = "shrecknet-dev-rsa-1"
    jwt_access_token_expiry_minutes: int = 60 * 24 * 365
    jwt_private_key_pem: str = ""
    jwt_public_key_pem: str = ""


class AppConfig(BaseModel):
    app_name: str
    debug: bool


def _config_db_path() -> Path:
    return _default_data_dir() / CONFIG_DB_FILENAME


def _legacy_config_db_path() -> Path:
    return _default_data_dir() / LEGACY_CONFIG_DB_FILENAME


def _connect() -> sqlite3.Connection:
    db_path = _config_db_path()
    legacy_path = _legacy_config_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists() and legacy_path.exists():
        legacy_path.replace(db_path)
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


def _explicit_env_overrides() -> dict[str, Any]:
    env_backed = Settings()
    overrides: dict[str, Any] = {}
    for field_name in BOOTSTRAP_ENV_FIELDS:
        env_key = f"SHRECKNET_{field_name.upper()}"
        if env_key in os.environ:
            overrides[field_name] = getattr(env_backed, field_name)
    return overrides


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
    return {**defaults, **existing}


def _normalize_legacy_database_urls(conn: sqlite3.Connection) -> None:
    current_values = _load_settings_from_db(conn)
    updates: dict[str, str] = {}

    expected_database_url = _sqlite_url(DEFAULT_DATABASE_FILENAME)
    database_url = current_values.get("database_url")
    if isinstance(database_url, str):
        if "backend_2.db" in database_url:
            updates["database_url"] = expected_database_url
        elif (
            DEFAULT_DATABASE_FILENAME in database_url
            and database_url != expected_database_url
            and "SHRECKNET_DATABASE_URL" not in os.environ
        ):
            # Normalize stale absolute host paths (e.g. /home/.../shrecknet.db)
            # to the current runtime data directory (e.g. /data/shrecknet.db).
            updates["database_url"] = expected_database_url

    expected_jobs_database_url = _sqlite_url(DEFAULT_JOBS_DATABASE_FILENAME)
    jobs_database_url = current_values.get("jobs_database_url")
    if isinstance(jobs_database_url, str):
        if "backend_2_jobs.db" in jobs_database_url:
            updates["jobs_database_url"] = expected_jobs_database_url
        elif (
            DEFAULT_JOBS_DATABASE_FILENAME in jobs_database_url
            and jobs_database_url != expected_jobs_database_url
            and "SHRECKNET_JOBS_DATABASE_URL" not in os.environ
        ):
            # Normalize stale absolute host paths (e.g. /home/.../shrecknet_jobs.db)
            # to the current runtime data directory (e.g. /data/shrecknet_jobs.db).
            updates["jobs_database_url"] = expected_jobs_database_url

    if updates:
        timestamp = _current_timestamp()
        conn.executemany(
            f"""
            INSERT INTO {CONFIG_TABLE} (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            [
                (key, _serialize_value(value), timestamp)
                for key, value in updates.items()
            ],
        )
        conn.commit()


def load_settings() -> Settings:
    conn = _connect()
    try:
        _ensure_schema(conn)
        _normalize_legacy_database_urls(conn)
        merged = _seed_defaults_if_needed(conn)
    finally:
        conn.close()
    merged.update(_explicit_env_overrides())
    return Settings(**merged)


def get_settings() -> Settings:
    global _settings_cache
    with _settings_lock:
        if _settings_cache is None:
            _settings_cache = load_settings()
        return _settings_cache


def reload_settings() -> Settings:
    global _settings_cache
    with _settings_lock:
        _settings_cache = load_settings()
        return _settings_cache


def update_settings(updates: dict[str, Any]) -> Settings:
    global _settings_cache
    with _settings_lock:
        current = _settings_cache or load_settings()
        settings_dict = current.model_dump()
        settings_dict.update(updates)
        updated = Settings(**settings_dict).model_dump()
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
        effective = {**updated, **_explicit_env_overrides()}
        _settings_cache = Settings(**effective)
        return _settings_cache


def get_app_config(current: Settings | None = None) -> AppConfig:
    s = current or get_settings()
    return AppConfig(app_name=s.app_name, debug=s.debug)


def is_openai_configured(current: Settings | None = None) -> bool:
    s = current or get_settings()
    raw_key = (s.openai_api_key or "").strip()
    return bool(raw_key and raw_key.lower() != "openaikey")
