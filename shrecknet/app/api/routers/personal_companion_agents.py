"""API router for user-owned personal companion agents."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session, get_media_service
from app.core.config_store import get_settings
from app.models.user import User
from app.schemas.personal_companion_agent import (
    PersonalCompanionAgentCreate,
    PersonalCompanionAgentRead,
    PersonalCompanionAgentUpdate,
)
from app.services.media_service import ImageValidationError, MediaService
from app.services.personal_companion_agent_service import PersonalCompanionAgentService

router = APIRouter(prefix="/users/me/companion", tags=["companions"])


async def get_companion_service(
    session: AsyncSession = Depends(get_db_session),
) -> PersonalCompanionAgentService:
    return PersonalCompanionAgentService(session)


@router.post("", response_model=PersonalCompanionAgentRead, status_code=status.HTTP_201_CREATED)
async def create_personal_companion(
    payload: PersonalCompanionAgentCreate,
    current_user: User = Depends(get_current_user),
    service: PersonalCompanionAgentService = Depends(get_companion_service),
) -> PersonalCompanionAgentRead:
    return await service.create_for_user(current_user.id, payload)


@router.get("", response_model=PersonalCompanionAgentRead)
async def get_personal_companion(
    current_user: User = Depends(get_current_user),
    service: PersonalCompanionAgentService = Depends(get_companion_service),
) -> PersonalCompanionAgentRead:
    return await service.get_for_user(current_user.id)


@router.patch("", response_model=PersonalCompanionAgentRead)
async def update_personal_companion(
    payload: PersonalCompanionAgentUpdate,
    current_user: User = Depends(get_current_user),
    service: PersonalCompanionAgentService = Depends(get_companion_service),
) -> PersonalCompanionAgentRead:
    return await service.update_for_user(current_user.id, payload)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_personal_companion(
    current_user: User = Depends(get_current_user),
    service: PersonalCompanionAgentService = Depends(get_companion_service),
) -> Response:
    await service.delete_for_user(current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/avatar", response_model=PersonalCompanionAgentRead)
async def upload_personal_companion_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    service: PersonalCompanionAgentService = Depends(get_companion_service),
    media_service: MediaService = Depends(get_media_service),
) -> PersonalCompanionAgentRead:
    companion = await service.get_for_user(current_user.id)

    try:
        settings = get_settings()
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", companion.name.lower()).strip("-")
        if not safe_name:
            safe_name = f"user-{current_user.id}"
        target_filename = f"companion_{safe_name}.png"
        avatar_url = await media_service.save_image(
            file,
            category="avatars",
            identifier=f"personal_companion_{current_user.id}",
            resize=(settings.image_max_width, settings.image_max_height),
            filename=target_filename,
        )
    except ImageValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return await service.update_for_user(
        current_user.id,
        PersonalCompanionAgentUpdate(avatar_url=avatar_url),
    )
