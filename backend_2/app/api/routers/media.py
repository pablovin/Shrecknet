from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import get_current_user, get_media_service, require_roles
from app.models.user import User, UserRole
from app.services.media_service import ImageValidationError, MediaService

router = APIRouter(prefix="/media", tags=["media"], dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER))])

VALID_CATEGORY_RE = re.compile(r"^[a-zA-Z0-9_\-./]+$")


def _sanitize_category(category: str) -> str:
    category = category.strip().lower().replace("..", "")
    if not category or not VALID_CATEGORY_RE.match(category):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid category name",
        )
    return category


@router.post("/images", status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile = File(...),
    category: str = Form("general"),
    identifier: str = Form("asset"),
    media_service: MediaService = Depends(get_media_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    safe_category = _sanitize_category(category)
    safe_identifier = identifier or "asset"

    try:
        url = await media_service.save_image(
            file,
            category=safe_category,
            identifier=f"{safe_identifier}_{current_user.id or 'system'}",
        )
    except ImageValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return {"url": url}
