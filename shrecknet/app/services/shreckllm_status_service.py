from __future__ import annotations

from typing import Any

import httpx

from app.core.config_store import Settings, is_shreckllm_configured


async def get_shreckllm_status(settings: Settings) -> dict[str, Any]:
    configured = is_shreckllm_configured(settings)
    if not configured:
        return {"configured": False, "reachable": False, "error": "not_configured"}

    base_url = str(settings.shreckllm_base_url or "").rstrip("/")
    timeout = float(settings.shreckllm_request_timeout_s or 10.0)
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
            health_resp = await client.get("/health")
            health_resp.raise_for_status()
            return {
                "configured": True,
                "reachable": True,
                "error": None,
            }
    except Exception:
        return {
            "configured": True,
            "reachable": False,
            "error": "unreachable",
        }


async def get_provider_validation(settings: Settings, provider_id: str) -> dict[str, Any]:
    base_url = str(settings.shreckllm_base_url or "").rstrip("/")
    timeout = float(settings.shreckllm_request_timeout_s or 10.0)
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
            response = await client.get("/status")
            response.raise_for_status()
            payload = response.json() if response.content else {}
            dependencies = payload.get("dependencies") if isinstance(payload, dict) else {}
            if not isinstance(dependencies, dict):
                dependencies = {}
            provider = dependencies.get(provider_id) if isinstance(dependencies.get(provider_id), dict) else {}
            return {
                "provider_id": provider_id,
                "valid": bool(provider.get("ok")) and bool(provider.get("default_model_available")),
                "error": None,
                "default_model": provider.get("default_model"),
                "default_model_available": provider.get("default_model_available"),
            }
    except Exception:
        return {"provider_id": provider_id, "valid": None, "error": "validation_unavailable"}
