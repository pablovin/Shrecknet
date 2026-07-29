from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_FILENAME = "shrecknet.db"
DEFAULT_JOBS_DATABASE_FILENAME = "shrecknet_jobs.db"
CONFIG_DB_FILENAME = "shrecknet_config.db"
LEGACY_CONFIG_DB_FILENAME = "configs.db"
CONFIG_TABLE = "config_settings"
DEFAULT_CONFIG_SEED_FILE = Path("/configs/shrecknet.initial.json")
BOOTSTRAP_ENV_FIELDS = frozenset(
    {
        "database_url",
        "jobs_database_url",
        "celery_broker_url",
        "celery_result_backend",
        "shreckllm_request_timeout_s",
        "shreckllm_max_retries",
        "jwt_private_key_pem",
        "jwt_public_key_pem",
        "neo4j_uri",
        "neo4j_user",
        "neo4j_password",
        "neo4j_database",
        "media_root",
        # Lets a deployment choose CUDA without mutating persistent config.
        "embedding_device",
    }
)

_settings_cache: "Settings | None" = None
_settings_lock = threading.Lock()
_data_dir_cache: Path | None = None
logger = logging.getLogger(__name__)

LLM_TARGET_FIELDS = (
    "model_architect_scene_chunking",
    "model_architect_entity_proposal",
    "model_architect_milestone_proposal",
    "model_architect_entity_generation",
    "model_agents_repair_json",
    "model_elder_planner",
    "model_elder_synthesis",
    "model_elder_character_incorporation",
    "model_novelist_planning",
    "model_novelist_prose",
    "model_novelist_critic",
    "model_librarian_planner",
    "model_librarian_synthesis",
    "model_librarian_character_incorporation",
    "model_character_agent_framing",
    "model_character_agent_deliberation",
    "model_character_agent_character_incorporation",
    "model_character_agent_scene_interpretation",
    "model_character_agent_update",
    "model_orchestrator_routing",
    "model_orchestrator_synthesis",
)
ALLOWED_OPENAI_MODELS = frozenset({"gpt-5", "gpt-5-nano", "gpt-4o-mini"})
LEGACY_EMBEDDING_MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
ACTIVE_EMBEDDING_MODEL_ID = "intfloat/multilingual-e5-small"


class LLMModelTarget(BaseModel):
    provider: str = "openai"
    name: str = "gpt-5-nano"

    @classmethod
    def from_legacy(cls, model_name: str) -> "LLMModelTarget":
        normalized = str(model_name or "").strip() or "gpt-5-nano"
        return cls(provider="openai", name=normalized)


class UserCreationMode(str, Enum):
    STOPPED = "stopped"
    MODERATED = "moderated"
    ALLOWED = "allowed"


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

    shreckllm_base_url: str = "http://shreckllm:8110"
    shreckllm_request_timeout_s: float = 60.0
    shreckllm_max_retries: int = 2
    llm_prewarm_on_startup: bool = True
    llm_prewarm_timeout_s: float = 300.0
    enable_ai_agents: bool = False
    user_creation_mode: UserCreationMode = UserCreationMode.MODERATED
    email_verification_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_tls_mode: str = "starttls"
    smtp_sender_email: str = ""
    smtp_sender_name: str = "Shrecknet"
    smtp_service_token: str = ""
    email_verification_frontend_url: str = ""
    email_verification_subject: str = "Confirm your Shrecknet account"
    email_verification_text_template: str = "Hello {{username}},\n\nConfirm your email address: {{verification_url}}"
    email_verification_html_template: str = "<p>Hello {{username}},</p><p><a href=\"{{verification_url}}\">Confirm your email address</a></p>"
    model_architect_scene_chunking: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="openai", name="gpt-5")
    )
    model_architect_entity_proposal: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="openai", name="gpt-5-nano")
    )
    model_architect_milestone_proposal: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="openai", name="gpt-5-nano")
    )
    model_architect_entity_generation: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="openai", name="gpt-5-nano")
    )
    model_agents_repair_json: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="openai", name="gpt-5-nano")
    )
    model_elder_planner: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="", name="")
    )
    model_elder_synthesis: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="", name="")
    )
    model_elder_character_incorporation: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="", name="")
    )
    model_novelist_planning: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="openai", name="gpt-5-nano")
    )
    model_novelist_prose: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="openai", name="gpt-5")
    )
    model_novelist_critic: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="openai", name="gpt-5-nano")
    )
    model_librarian_planner: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="", name="")
    )
    model_librarian_synthesis: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="", name="")
    )
    model_librarian_character_incorporation: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="", name="")
    )
    model_character_agent_framing: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="", name="")
    )
    model_character_agent_deliberation: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="", name="")
    )
    model_character_agent_character_incorporation: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="", name="")
    )
    model_character_agent_scene_interpretation: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="", name="")
    )
    model_character_agent_update: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="", name="")
    )
    character_agent_embodiment_evidence_tokens: int = Field(12_000, ge=1_000, le=100_000)
    character_agent_embodiment_max_aspects: int = Field(12, ge=0, le=50)
    character_agent_embodiment_max_goals: int = Field(8, ge=0, le=50)
    librarian_debug_artifacts_enabled: bool = True
    elder_debug_artifacts_enabled: bool = True
    model_orchestrator_routing: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="openai", name="gpt-5-nano")
    )
    model_orchestrator_synthesis: LLMModelTarget = Field(
        default_factory=lambda: LLMModelTarget(provider="openai", name="gpt-5")
    )
    companion_agent_trace_enabled: bool = False
    embedding_model_id: str = "intfloat/multilingual-e5-small"
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
    semantic_embedding_strategy: str = "v2"
    semantic_embedding_version: str = "semantic-v2.0"
    semantic_embedding_long_text_threshold_tokens: int = 512
    semantic_embedding_chunk_target_tokens: int = 384
    semantic_embedding_chunk_overlap_tokens: int = 64
    embedding_chunk_size: int = 900
    embedding_chunk_overlap: int = 150
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


def _repo_config_seed_file() -> Path:
    return Path(__file__).resolve().parents[3] / "configs" / "shrecknet.initial.json"


def _config_seed_file_candidates() -> list[Path]:
    configured = os.getenv("SHRECKNET_CONFIG_SEED_FILE")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend([DEFAULT_CONFIG_SEED_FILE, _repo_config_seed_file()])
    return candidates


def _load_initial_settings_file() -> dict[str, Any]:
    for path in _config_seed_file_candidates():
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid Shrecknet config seed file '{path}': {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid Shrecknet config seed file '{path}': expected JSON object")
        allowed = set(Settings.model_fields)
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(
                f"Invalid Shrecknet config seed file '{path}': unknown keys {', '.join(unknown)}"
            )
        logger.info("Loaded Shrecknet config seed file: %s", path)
        return payload
    return {}


def _default_settings_dict() -> dict[str, Any]:
    defaults = Settings().model_dump()
    defaults.update(_load_initial_settings_file())
    return Settings(**defaults).model_dump()


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
    # Env-only bootstrap fields are sourced from environment and must not be seeded into DB.
    missing = {
        key: value
        for key, value in defaults.items()
        if key not in existing and key not in BOOTSTRAP_ENV_FIELDS
    }
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


def _normalize_legacy_llm_targets(conn: sqlite3.Connection) -> None:
    current_values = _load_settings_from_db(conn)
    updates: dict[str, dict[str, str]] = {}
    for field_name in LLM_TARGET_FIELDS:
        raw_value = current_values.get(field_name)
        if isinstance(raw_value, str):
            updates[field_name] = LLMModelTarget.from_legacy(raw_value).model_dump()
            continue
        if isinstance(raw_value, dict):
            provider = str(raw_value.get("provider") or "").strip()
            name = str(raw_value.get("name") or "").strip()
            if not provider and not name:
                continue
            if provider and name:
                continue
            normalized = LLMModelTarget(
                provider=provider or "openai",
                name=name or "gpt-5-nano",
            )
            updates[field_name] = normalized.model_dump()
    for field_name in LLM_TARGET_FIELDS:
        raw_value = current_values.get(field_name)
        if not isinstance(raw_value, dict):
            continue
        provider = str(raw_value.get("provider") or "").strip() or "openai"
        name = str(raw_value.get("name") or "").strip() or "gpt-5-nano"
        if not str(raw_value.get("provider") or "").strip() and not str(
            raw_value.get("name") or ""
        ).strip():
            continue
        normalized_name = name
        if provider == "openai" and name not in ALLOWED_OPENAI_MODELS:
            normalized_name = "gpt-5-nano"
        normalized = LLMModelTarget(provider=provider, name=normalized_name).model_dump()
        if raw_value != normalized:
            updates[field_name] = normalized

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


def _migrate_embedding_model(conn: sqlite3.Connection) -> None:
    """Move the former default to E5 while preserving an explicit custom model."""
    current_values = _load_settings_from_db(conn)
    if current_values.get("embedding_model_id") != LEGACY_EMBEDDING_MODEL_ID:
        return
    timestamp = _current_timestamp()
    conn.execute(
        f"""
        INSERT INTO {CONFIG_TABLE} (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        ("embedding_model_id", _serialize_value(ACTIVE_EMBEDDING_MODEL_ID), timestamp),
    )
    conn.commit()
    logger.info("Migrated embedding_model_id from %s to %s", LEGACY_EMBEDDING_MODEL_ID, ACTIVE_EMBEDDING_MODEL_ID)


def load_settings() -> Settings:
    conn = _connect()
    try:
        _ensure_schema(conn)
        _normalize_legacy_database_urls(conn)
        _normalize_legacy_llm_targets(conn)
        _migrate_embedding_model(conn)
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
        for field_name in LLM_TARGET_FIELDS:
            if field_name not in settings_dict:
                continue
            raw_value = settings_dict.get(field_name)
            if isinstance(raw_value, str):
                raise ValueError(
                    f"{field_name} must be an object with provider/name; legacy string values are not accepted"
                )
            elif isinstance(raw_value, dict):
                provider = str(raw_value.get("provider") or "").strip()
                name = str(raw_value.get("name") or "").strip()
                # A blank target is the intentional pre-provider startup state.
                # It is filled when Agents is enabled and shreckLLM has a usable model.
                if not provider and not name:
                    settings_dict[field_name] = LLMModelTarget(provider="", name="").model_dump()
                    continue
                if (provider or "openai") == "openai" and (name or "gpt-5-nano") not in ALLOWED_OPENAI_MODELS:
                    name = "gpt-5-nano"
                settings_dict[field_name] = LLMModelTarget(
                    provider=provider or "openai",
                    name=name or "gpt-5-nano",
                ).model_dump()
        updated = Settings(**settings_dict).model_dump()
        # Env-only bootstrap fields must never be persisted in runtime DB settings.
        db_updated = {k: v for k, v in updated.items() if k not in BOOTSTRAP_ENV_FIELDS}
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
                    for key, value in db_updated.items()
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


def is_shreckllm_configured(current: Settings | None = None) -> bool:
    s = current or get_settings()
    return bool(str(s.shreckllm_base_url or "").strip())
