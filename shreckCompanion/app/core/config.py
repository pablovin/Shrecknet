from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_SEED_FILE = Path("/configs/shreckcompanion.json")


class ModelReference(BaseModel):
    provider: str = Field(min_length=1)
    name: str = Field(min_length=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHRECKCOMPANION_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8120
    data_dir: str = str(Path(__file__).resolve().parents[1] / "databases")
    media_root: str = str(Path(__file__).resolve().parents[1] / "media")
    media_base_url: str = "/media"
    max_image_upload_bytes: int = 5 * 1024 * 1024
    image_max_width: int = 1024
    image_max_height: int = 1024
    shreckllm_base_url: str = "http://localhost:8111"
    shrecknet_api_base_url: str = "http://localhost:8100"
    internal_service_token: str = ""
    default_user_id: int = 1
    model_personal_companion_routing: ModelReference = ModelReference(
        provider="ollama_cloud",
        name="gemma3:4b",
    )
    model_personal_companion_synthesis: ModelReference = ModelReference(
        provider="ollama_cloud",
        name="gemma3:4b",
    )
    routing_temperature: float = 0.0
    synthesis_temperature: float = 0.3
    turn_query_max_length: int = 3000
    conversation_recent_messages_limit: int = 6
    conversation_summary_trigger_messages: int = 10
    conversation_context_char_limit: int = 4000
    companion_chat_session_limit_per_ontology: int = 10
    provider_timeout_seconds: float = 120.0
    turn_job_result_ttl_seconds: int = 3600
    cors_allow_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8100",
        "http://localhost:8121",
    ]
    cors_allow_methods: list[str] = ["*"]
    cors_allow_headers: list[str] = ["*"]
    cors_allow_credentials: bool = True

    @property
    def database_path(self) -> Path:
        return Path(self.data_dir) / "shreckcompanion.sqlite3"

    @property
    def chats_dir(self) -> Path:
        return Path(self.data_dir) / "chats"

    @property
    def local_tests_dir(self) -> Path:
        return Path(self.data_dir) / "local_tests"

    @property
    def media_path(self) -> Path:
        return Path(self.media_root)


def _repo_config_seed_file() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "shreckcompanion.json"


def _config_seed_file_candidates() -> list[Path]:
    configured = os.getenv("SHRECKCOMPANION_CONFIG_SEED_FILE")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend([DEFAULT_CONFIG_SEED_FILE, _repo_config_seed_file()])
    return candidates


def _load_initial_settings_file() -> dict[str, Any]:
    for path in _config_seed_file_candidates():
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid ShreckCompanion config seed file '{path}': expected JSON object")
        unknown = sorted(set(payload) - set(Settings.model_fields))
        if unknown:
            raise ValueError(
                f"Invalid ShreckCompanion config seed file '{path}': unknown keys {', '.join(unknown)}"
            )
        return payload
    return {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(**_load_initial_settings_file())
