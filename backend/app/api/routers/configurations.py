from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_admin_user
from app.celery_app import configure_celery_app
from app.core.config_store import Settings, get_settings, reload_settings, update_settings

router = APIRouter(prefix="/config", tags=["config"])


def _validate_updates(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = set(Settings.model_fields)
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown config keys: {', '.join(sorted(unknown))}",
        )
    return payload


@router.get(
    "/",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
def get_config() -> dict[str, Any]:
    return get_settings().model_dump()


@router.put(
    "/",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
def put_config(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Config payload must be an object",
        )
    updates = _validate_updates(payload)
    settings = update_settings(updates)
    configure_celery_app()
    return settings.model_dump()


@router.post(
    "/reload",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
def reload_config() -> dict[str, Any]:
    settings = reload_settings()
    configure_celery_app()
    return settings.model_dump()
