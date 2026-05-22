from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_SEED_FILE = Path("/configs/shreckllm.initial.json")


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

    bootstrap_provider_defaults: dict[str, dict[str, Any]] = {
        "ollama": {
            "default_model": "gemma3:4b",
            "models": ["gemma3:4b"],
            "base_url": "http://ollama:11434",
            "api_key": None,
        },
        "openai": {
            "default_model": "gpt-5-nano",
            "models": ["gpt-5-nano", "gpt-5", "gpt-4o-mini"],
            "base_url": None,
            "api_key": "",
        },
        "anthropic": {
            "default_model": "claude-3-haiku-20240307",
            "models": ["claude-3-haiku-20240307", "claude-opus-4-1-20250805"],
            "base_url": "https://api.anthropic.com",
            "api_key": "",
        },
    }

    bootstrap_memory_ttl_seconds: int = 3600
    bootstrap_memory_max_messages: int = 24
    bootstrap_max_concurrent_requests: int = 8
    bootstrap_request_timeout_seconds: float = 45.0
    bootstrap_max_queue_wait_seconds: float = 10.0
    ollama_keep_alive: str = "30m"
    ollama_prewarm_on_startup: bool = True


def _repo_config_seed_file() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "shreckllm.initial.json"


def _config_seed_file_candidates() -> list[Path]:
    configured = os.getenv("SHRECKLLM_CONFIG_SEED_FILE")
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
            raise ValueError(f"Invalid shreckLLM config seed file '{path}': {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid shreckLLM config seed file '{path}': expected JSON object")
        allowed = set(Settings.model_fields)
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(
                f"Invalid shreckLLM config seed file '{path}': unknown keys {', '.join(unknown)}"
            )
        return payload
    return {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(**_load_initial_settings_file())
