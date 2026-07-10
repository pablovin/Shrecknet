from __future__ import annotations

from typing import Any

import httpx

from app.core.config_store import Settings, is_shreckllm_configured


async def get_shreckllm_status(settings: Settings) -> dict[str, Any]:
    configured = is_shreckllm_configured(settings)
    if not configured:
        return {"configured": False, "reachable": False, "operational": False, "error": "not_configured"}

    base_url = str(settings.shreckllm_base_url or "").rstrip("/")
    timeout = float(settings.shreckllm_request_timeout_s or 10.0)
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
            status_resp = await client.get("/status")
            status_resp.raise_for_status()
            payload = status_resp.json() if status_resp.content else {}
            operational = bool(payload.get("shreckllm_operational")) if isinstance(payload, dict) else False
            return {
                "configured": True,
                "reachable": True,
                "operational": operational,
                "error": None,
            }
    except Exception:
        return {
            "configured": True,
            "reachable": False,
            "operational": False,
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
            active = bool(provider.get("ok")) and bool(provider.get("any_configured_model_available"))
            return {
                "provider_id": provider_id,
                "active": active,
                "error": None,
                "configured_models": provider.get("configured_models"),
                "any_configured_model_available": provider.get("any_configured_model_available"),
            }
    except Exception:
        return {"provider_id": provider_id, "active": None, "error": "validation_unavailable"}


async def get_all_provider_validations(settings: Settings) -> dict[str, Any]:
    base_url = str(settings.shreckllm_base_url or "").rstrip("/")
    timeout = float(settings.shreckllm_request_timeout_s or 10.0)
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
            response = await client.get("/providers")
            response.raise_for_status()
            payload = response.json() if response.content else {}
            providers = payload.get("providers") if isinstance(payload, dict) else {}
            if not isinstance(providers, dict):
                providers = {}
            operational_provider_ids = payload.get("operational_provider_ids") if isinstance(payload, dict) else []
            if not isinstance(operational_provider_ids, list):
                operational_provider_ids = []
            return {
                "shreckllm_operational": bool(payload.get("shreckllm_operational")) if isinstance(payload, dict) else False,
                "operational_provider_ids": [str(provider_id) for provider_id in operational_provider_ids],
                "providers": providers,
                "error": None,
            }
    except Exception:
        return {
            "shreckllm_operational": False,
            "operational_provider_ids": [],
            "providers": {},
            "error": "validation_unavailable",
        }


async def get_provider_model_catalog(settings: Settings) -> dict[str, Any]:
    base_url = str(settings.shreckllm_base_url or "").rstrip("/")
    timeout = float(settings.shreckllm_request_timeout_s or 10.0)
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
            response = await client.get("/models")
            response.raise_for_status()
            payload = response.json() if response.content else {}
            providers = payload.get("providers") if isinstance(payload, dict) else {}
            if not isinstance(providers, dict):
                providers = {}

            clean_providers: dict[str, dict[str, list[str]]] = {}
            for provider_id, raw in providers.items():
                if not isinstance(raw, dict):
                    continue
                configured_models = raw.get("configured_models")
                discovered_models = raw.get("discovered_models")
                models = raw.get("models")
                clean_providers[str(provider_id)] = {
                    "configured_models": [str(model) for model in configured_models] if isinstance(configured_models, list) else [],
                    "discovered_models": [str(model) for model in discovered_models] if isinstance(discovered_models, list) else [],
                    "models": [str(model) for model in models] if isinstance(models, list) else [],
                }

            return {"providers": clean_providers, "error": None}
    except Exception:
        return {"providers": {}, "error": "model_catalog_unavailable"}
