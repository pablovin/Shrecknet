from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import get_current_user
from app.core.config_store import get_settings
from app.services.shreckllm_status_service import (
    get_provider_validation,
    get_shreckllm_status,
)

router = APIRouter(prefix="/llm_status", tags=["llm_status"])


@router.get("/", status_code=status.HTTP_200_OK)
async def get_service_status(
    _current_user=Depends(get_current_user),
) -> dict[str, object]:
    settings = get_settings()
    shreckllm_status = await get_shreckllm_status(settings)
    openai_status = await get_provider_validation(settings, "openai")
    shreckllm_reachable = shreckllm_status.get("reachable") is True
    openai_available = shreckllm_reachable and openai_status.get("valid") is True

    return {
        "shreckllm": shreckllm_status,
        "openai": openai_status,
        "services": {
            "architect": shreckllm_reachable,
            "elder": shreckllm_reachable,
            "librarian": shreckllm_reachable,
            "novelist": shreckllm_reachable,
        },
        "providers": {
            "openai": openai_available,
        },
    }
