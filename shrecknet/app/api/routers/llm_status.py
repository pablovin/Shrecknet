from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import get_current_user
from app.core.config_store import get_settings
from app.services.shreckllm_status_service import (
    get_all_provider_validations,
    get_shreckllm_status,
)

router = APIRouter(prefix="/llm_status", tags=["llm_status"])


@router.get("/", status_code=status.HTTP_200_OK)
async def get_service_status(
    _current_user=Depends(get_current_user),
) -> dict[str, object]:
    settings = get_settings()
    shreckllm_status = await get_shreckllm_status(settings)
    provider_validations = await get_all_provider_validations(settings)
    shreckllm_reachable = shreckllm_status.get("reachable") is True
    providers = provider_validations.get("providers") if isinstance(provider_validations, dict) else {}
    if not isinstance(providers, dict):
        providers = {}

    def _target_status(target) -> dict[str, object]:  # type: ignore[no-untyped-def]
        provider_id = getattr(target, "provider", "openai")
        model_name = getattr(target, "name", "")
        provider_payload = providers.get(provider_id)
        provider_valid = bool(provider_payload.get("valid")) if isinstance(provider_payload, dict) else False
        model_valid = None
        if isinstance(provider_payload, dict):
            models = provider_payload.get("models")
            if isinstance(models, list):
                for item in models:
                    if isinstance(item, dict) and item.get("model") == model_name:
                        model_valid = bool(item.get("valid"))
                        break
        return {
            "provider": provider_id,
            "model": model_name,
            "provider_valid": provider_valid,
            "model_valid": model_valid,
            "valid": provider_valid and (model_valid is not False),
        }

    return {
        "shreckllm": shreckllm_status,
        "services": {
            "architect": shreckllm_reachable,
            "elder": shreckllm_reachable,
            "librarian": shreckllm_reachable,
            "novelist": shreckllm_reachable,
        },
        "providers": providers,
        "model_targets": {
            "model_architect_scene_chunking": _target_status(settings.model_architect_scene_chunking),
            "model_architect": _target_status(settings.model_architect),
            "model_elder": _target_status(settings.model_elder),
            "model_novelist": _target_status(settings.model_novelist),
            "model_novelist_draft": _target_status(settings.model_novelist_draft),
            "model_librarian": _target_status(settings.model_librarian),
        },
    }
