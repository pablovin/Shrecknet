from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.deps import get_current_admin_user
from app.celery_app import configure_celery_app
from app.core.config_store import Settings, get_settings, reload_settings, update_settings

router = APIRouter(prefix="/config", tags=["config"])

MAX_GOOGLE_SERVICE_ACCOUNT_BYTES = 1024 * 1024
GOOGLE_SERVICE_DIR = Path("secrets") / "google"
GOOGLE_SERVICE_FILENAME = "service_account.json"


def _validate_updates(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = set(Settings.model_fields)
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown config keys: {', '.join(sorted(unknown))}",
        )
    return payload


def _google_service_account_path(settings: Settings) -> Path:
    base = Path(settings.media_root)
    target_dir = base / GOOGLE_SERVICE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / GOOGLE_SERVICE_FILENAME


async def _save_google_service_account(
    upload: UploadFile,
    settings: Settings,
) -> Path:
    if not upload.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing filename for uploaded service account JSON",
        )
    contents = await upload.read()
    if len(contents) > MAX_GOOGLE_SERVICE_ACCOUNT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Service account JSON exceeds size limit",
        )
    try:
        json.loads(contents)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload for service account",
        ) from exc

    target_path = _google_service_account_path(settings)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "wb") as handle:
        handle.write(contents)
    try:
        os.chmod(target_path, 0o600)
    except OSError:
        # Best-effort on platforms without chmod support
        pass
    return target_path


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


@router.get(
    "/google-calendar",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
def get_google_calendar_config() -> dict[str, Any]:
    settings = get_settings()
    service_account_configured = bool(settings.google_service_account_json)
    return {
        "activate_google_calendar": settings.activate_google_calendar,
        "service_account_configured": service_account_configured,
        "service_account_path": settings.google_service_account_json,
        "effective_enabled": settings.activate_google_calendar
        and service_account_configured,
    }


@router.put(
    "/google-calendar",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
def update_google_calendar_config(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Config payload must be an object",
        )
    if "activate_google_calendar" not in payload:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="activate_google_calendar is required",
        )
    if not isinstance(payload["activate_google_calendar"], bool):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="activate_google_calendar must be a boolean",
        )
    settings = update_settings(
        {"activate_google_calendar": payload["activate_google_calendar"]}
    )
    configure_celery_app()
    return {
        "activate_google_calendar": settings.activate_google_calendar,
        "service_account_configured": bool(settings.google_service_account_json),
        "service_account_path": settings.google_service_account_json,
        "effective_enabled": settings.activate_google_calendar
        and bool(settings.google_service_account_json),
    }


@router.post(
    "/google-calendar/service-account",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
async def upload_google_calendar_service_account(
    file: UploadFile = File(...),
) -> dict[str, Any]:
    settings = get_settings()
    stored_path = await _save_google_service_account(file, settings)
    settings = update_settings({"google_service_account_json": str(stored_path)})
    configure_celery_app()
    return {
        "service_account_configured": True,
        "service_account_path": settings.google_service_account_json,
        "activate_google_calendar": settings.activate_google_calendar,
        "effective_enabled": settings.activate_google_calendar,
    }
