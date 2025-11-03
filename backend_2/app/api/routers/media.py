from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import (
    get_current_user,
    get_media_service,
    require_roles,
)
from app.core.config import get_settings
from app.models.user import User, UserRole
from app.services.media_service import (
    ImageValidationError,
    MediaService,
    PdfValidationError,
)

router = APIRouter(
    prefix="/media-admin",
    tags=["media"],
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER))],
)

settings = get_settings()

_COMPONENT_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")


def _sanitize_component(value: str, *, field: str, to_lower: bool = False) -> str:
    cleaned = value.strip()
    if to_lower:
        cleaned = cleaned.lower()
    # Check for path traversal attempts before removing them
    if ".." in cleaned or "/" in cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field}",
        )
    if not cleaned or not _COMPONENT_RE.match(cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field}",
        )
    return cleaned


@router.post("/images", status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile = File(...),
    content_type: str = Form(...),
    content_id: str = Form(...),
    is_main: bool = Form(False),
    media_service: MediaService = Depends(get_media_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """
    Upload an image for a specific content type and ID.

    Args:
        file: The image file to upload
        content_type: String identifying the content type (e.g., 'user', 'avatar', 'post')
        content_id: String identifying the specific content instance
        is_main: If True, saves as 'file.png' (overwrites); if False, uses incremental naming

    Returns:
        Dictionary containing the URL to the uploaded image
    """
    # Sanitize content_type to ensure it's safe for filesystem
    safe_content_type = _sanitize_component(
        content_type, field="content_type", to_lower=True
    )

    # Sanitize content_id
    safe_content_id = _sanitize_component(content_id, field="content_id")

    try:
        url = await media_service.save_content_image(
            file,
            content_type=safe_content_type,
            content_id=safe_content_id,
            is_main=is_main,
            resize=(settings.image_max_width, settings.image_max_height),
        )
    except ImageValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return {"url": url}


@router.post("/pdfs", status_code=status.HTTP_201_CREATED)
async def upload_pdf(
    file: UploadFile = File(...),
    content_id: str = Form(...),
    media_service: MediaService = Depends(get_media_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """
    Upload a PDF associated with a content record.
    """
    safe_content_id = _sanitize_component(content_id, field="content_id")

    try:
        url = await media_service.save_content_pdf(
            file,
            content_id=safe_content_id,
        )
    except PdfValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return {"url": url}
