from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHRECKLLM_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8110

    # Infrastructure/static wiring
    redis_url: str = "redis://localhost:6379/2"
    shrecknet_api_base_url: str = "http://localhost:8100"
    cors_allow_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8100",
    ]
    cors_allow_methods: list[str] = ["*"]
    cors_allow_headers: list[str] = ["*"]
    cors_allow_credentials: bool = True

    data_dir: str = str(Path(__file__).resolve().parents[1] / "databases")

    # Bootstrap defaults for runtime config (persisted in sqlite)
    bootstrap_default_provider_id: str = "ollama"

    bootstrap_provider_defaults: dict[str, dict[str, str | None]] = {
        "ollama": {
            "default_model": "gemma3:4b",
            "base_url": "http://localhost:11434",
            "api_key": None,
        },
        "openai": {
            "default_model": "gpt-5-nano",
            "base_url": None,
            "api_key": "",
        },
    }

    bootstrap_memory_ttl_seconds: int = 3600
    bootstrap_memory_max_messages: int = 24
    bootstrap_max_concurrent_requests: int = 8
    bootstrap_request_timeout_seconds: float = 45.0
    bootstrap_max_queue_wait_seconds: float = 10.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
