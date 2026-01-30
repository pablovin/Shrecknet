from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import get_current_user
from app.core.config_store import get_settings, is_openai_configured
from app.services.openai_status_service import get_openai_status

router = APIRouter(prefix="/llm_status", tags=["llm_status"])


@router.get("/", status_code=status.HTTP_200_OK)
async def get_service_status(
    _current_user=Depends(get_current_user),
) -> dict[str, object]:
    settings = get_settings()
    openai_status = await get_openai_status(settings)
    openai_available = (
        is_openai_configured(settings) and openai_status.get("valid") is True
    )

    return {
        "openai": openai_status,
        "services": {
            "architect": openai_available,
            "elder": openai_available,
            "librarian": openai_available,
            "novelist": openai_available,
        },
    }
