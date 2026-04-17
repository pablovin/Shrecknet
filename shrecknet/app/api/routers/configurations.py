from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import get_current_active_admin_or_world_builder, get_current_admin_user
from app.celery_app import configure_celery_app
from app.core.config_store import Settings, get_settings, reload_settings, update_settings

router = APIRouter(prefix="/config", tags=["config"])
logger = logging.getLogger(__name__)

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
        logger.warning("Google Calendar service account upload missing filename")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing filename for uploaded service account JSON",
        )
    contents = await upload.read()
    if len(contents) > MAX_GOOGLE_SERVICE_ACCOUNT_BYTES:
        logger.warning(
            "Google Calendar service account upload too large: %d bytes",
            len(contents),
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Service account JSON exceeds size limit",
        )
    try:
        json.loads(contents)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Google Calendar service account upload invalid JSON: %s",
            exc,
        )
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


def _get_config_payload() -> dict[str, Any]:
    return get_settings().model_dump()


def _google_service_account_metadata(settings: Settings) -> dict[str, Any]:
    path = _google_service_account_path(settings)
    exists = path.exists() and path.is_file()
    return {
        "service_account_configured": exists,
        "service_account_path": str(path) if exists else None,
    }


@router.get(
    "",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def get_config_no_slash() -> dict[str, Any]:
    return _get_config_payload()


@router.get(
    "/",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
def get_config() -> dict[str, Any]:
    return _get_config_payload()


def _put_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Config payload must be an object",
        )
    updates = _validate_updates(payload)
    settings = update_settings(updates)
    configure_celery_app()
    return settings.model_dump()


@router.put(
    "",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def put_config_no_slash(payload: dict[str, Any]) -> dict[str, Any]:
    return _put_config_payload(payload)


@router.put(
    "/",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
def put_config(payload: dict[str, Any]) -> dict[str, Any]:
    return _put_config_payload(payload)


@router.post(
    "/reload",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
def reload_config() -> dict[str, Any]:
    settings = reload_settings()
    configure_celery_app()
    return settings.model_dump()


@router.post(
    "/google-service-account",
    dependencies=[Depends(get_current_active_admin_or_world_builder)],
    status_code=status.HTTP_200_OK,
)
async def upload_google_service_account(
    file: UploadFile = File(...),
    request: Request = None,
) -> dict[str, Any]:
    settings = get_settings()
    if request is not None:
        logger.info(
            "Google Calendar service account upload headers: %s",
            dict(request.headers),
        )
    try:
        stored_path = await _save_google_service_account(file, settings)
    except HTTPException as exc:
        logger.warning("Google service account upload failed: %s (filename=%s, content_type=%s)", exc.detail, file.filename, file.content_type)
        raise
    return {
        "service_account_configured": True,
        "service_account_path": str(stored_path),
    }


@router.get(
    "/google-service-account",
    dependencies=[Depends(get_current_active_admin_or_world_builder)],
    status_code=status.HTTP_200_OK,
)
def get_google_service_account_metadata() -> dict[str, Any]:
    settings = get_settings()
    return _google_service_account_metadata(settings)


@router.get(
    "/google-service-account/file",
    dependencies=[Depends(get_current_active_admin_or_world_builder)],
    status_code=status.HTTP_200_OK,
)
def download_google_service_account() -> FileResponse:
    settings = get_settings()
    path = _google_service_account_path(settings)
    if not path.exists() or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google service account file not found",
        )
    return FileResponse(
        path,
        media_type="application/json",
        filename=path.name,
    )
