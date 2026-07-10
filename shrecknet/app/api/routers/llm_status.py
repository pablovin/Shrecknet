from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import get_current_user
from app.core.config_store import get_settings
from app.services.shreckllm_status_service import (
    get_all_provider_validations,
    get_provider_model_catalog,
    get_shreckllm_status,
)

router = APIRouter(prefix="/llm_status", tags=["llm_status"])


@router.get("/models", status_code=status.HTTP_200_OK)
async def get_service_model_catalog(
    _current_user=Depends(get_current_user),
) -> dict[str, object]:
    settings = get_settings()
    return await get_provider_model_catalog(settings)


@router.get("/", status_code=status.HTTP_200_OK)
async def get_service_status(
    _current_user=Depends(get_current_user),
) -> dict[str, object]:
    settings = get_settings()
    shreckllm_status = await get_shreckllm_status(settings)
    provider_validations = await get_all_provider_validations(settings)
    shreckllm_reachable = shreckllm_status.get("reachable") is True
    shreckllm_operational = bool(provider_validations.get("shreckllm_operational")) if isinstance(provider_validations, dict) else False
    operational_provider_ids = provider_validations.get("operational_provider_ids") if isinstance(provider_validations, dict) else []
    if not isinstance(operational_provider_ids, list):
        operational_provider_ids = []
    providers = provider_validations.get("providers") if isinstance(provider_validations, dict) else {}
    if not isinstance(providers, dict):
        providers = {}
    shreckllm_status = {
        **shreckllm_status,
        "operational": shreckllm_reachable and shreckllm_operational,
        "operational_provider_ids": [str(provider_id) for provider_id in operational_provider_ids],
    }

    def _target_status(target) -> dict[str, object]:  # type: ignore[no-untyped-def]
        provider_id = getattr(target, "provider", "openai")
        model_name = getattr(target, "name", "")
        provider_payload = providers.get(provider_id)
        provider_active = bool(provider_payload.get("active")) if isinstance(provider_payload, dict) else False
        model_available = None
        if isinstance(provider_payload, dict):
            models = provider_payload.get("models")
            if isinstance(models, list):
                for item in models:
                    if isinstance(item, dict) and item.get("model") == model_name:
                        model_available = bool(item.get("available", item.get("valid")))
                        break
        return {
            "provider": provider_id,
            "model": model_name,
            "provider_active": provider_active,
            "model_available": model_available,
            "active": provider_active and (model_available is not False),
        }

    return {
        "shreckllm_operational": shreckllm_status["operational"],
        "shreckllm": shreckllm_status,
        "services": {
            "architect": shreckllm_status["operational"],
            "elder": shreckllm_status["operational"],
            "librarian": shreckllm_status["operational"],
            "novelist": shreckllm_status["operational"],
        },
        "providers": providers,
        "model_targets": {
            "model_architect_scene_chunking": _target_status(settings.model_architect_scene_chunking),
            "model_architect_entity_proposal": _target_status(settings.model_architect_entity_proposal),
            "model_architect_milestone_proposal": _target_status(settings.model_architect_milestone_proposal),
            "model_architect_entity_generation": _target_status(settings.model_architect_entity_generation),
            "model_agents_repair_json": _target_status(settings.model_agents_repair_json),
            "model_elder": _target_status(settings.model_elder),
            "model_novelist_planning": _target_status(settings.model_novelist_planning),
            "model_novelist_prose": _target_status(settings.model_novelist_prose),
            "model_novelist_critic": _target_status(settings.model_novelist_critic),
            "model_librarian": _target_status(settings.model_librarian),
        },
    }
