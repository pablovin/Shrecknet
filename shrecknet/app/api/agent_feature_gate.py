from __future__ import annotations

from fastapi import HTTPException, status

from app.core.config_store import get_settings


def require_ai_agents_enabled() -> None:
    settings = get_settings()
    if settings.enable_ai_agents:
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="AI agents are disabled",
    )
