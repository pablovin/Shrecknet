from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.config_store import LLMModelTarget, Settings


async def fetch_shreckllm_runtime(settings: Settings) -> dict[str, Any]:
    base_url = str(settings.shreckllm_base_url or "").rstrip("/")
    timeout = float(settings.shreckllm_request_timeout_s or 10.0)
    token = str(os.getenv("SHRECKLLM_INTERNAL_SERVICE_TOKEN") or "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        response = await client.get("/config", headers=headers)
        response.raise_for_status()
        payload = response.json() if response.content else {}
        return payload if isinstance(payload, dict) else {}


def resolve_provider_default_target(
    runtime_config: dict[str, Any],
    provider_id: str | None = None,
) -> LLMModelTarget:
    providers = runtime_config.get("provider_defaults")
    if not isinstance(providers, dict) or not providers:
        raise RuntimeError("shreckLLM runtime missing provider_defaults")
    chosen_provider = str(provider_id or "").strip() or next(iter(providers.keys()))
    provider_payload = providers.get(chosen_provider)
    if not isinstance(provider_payload, dict):
        raise RuntimeError(f"Provider '{chosen_provider}' not configured in shreckLLM runtime")
    model_name = str(provider_payload.get("default_model") or "").strip()
    if not model_name:
        raise RuntimeError(f"Provider '{chosen_provider}' has no default_model in shreckLLM runtime")
    return LLMModelTarget(provider=chosen_provider, name=model_name)


def resolve_effective_architect_concurrency(runtime_config: dict[str, Any], *, provider_id: str) -> int:
    global_max = int(runtime_config.get("max_concurrent_requests") or 0)
    if global_max <= 0:
        raise RuntimeError("Invalid shreckLLM runtime max_concurrent_requests")
    provider_limits = runtime_config.get("provider_limits")
    if isinstance(provider_limits, dict):
        limit_payload = provider_limits.get(provider_id)
        if isinstance(limit_payload, dict):
            provider_max = int(limit_payload.get("max_concurrent") or 0)
            if provider_max > 0:
                return max(1, min(global_max, provider_max))
    return max(1, global_max)
