from __future__ import annotations

from fastapi import HTTPException, status

from app.core.config_store import get_settings
from app.services.shreckllm_status_service import get_all_provider_validations

AGENTS_DISABLED_DETAIL = "Agents are disabled. Enable Agents in runtime configuration before starting agent jobs."
AGENTS_ENABLE_REQUIRES_SHRECKLLM_DETAIL = (
    "Enable Agents can only be turned on when shreckLLM is operational "
    "(ready endpoint returns ready=true)."
)


async def require_shreckllm_operational_for_agents_enable() -> None:
    settings = get_settings()
    validations = await get_all_provider_validations(settings)
    if validations.get("shreckllm_operational") is True:
        return
    error = validations.get("error")
    suffix = f" Current shreckLLM status: {error}." if error else ""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"{AGENTS_ENABLE_REQUIRES_SHRECKLLM_DETAIL}{suffix}",
    )


def require_ai_agents_enabled() -> None:
    settings = get_settings()
    if settings.enable_ai_agents:
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=AGENTS_DISABLED_DETAIL,
    )
