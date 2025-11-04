from __future__ import annotations

import re
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)

from app.api.deps import (
    get_audit_service,
    get_current_user,
    get_user_service,
    get_media_service,
    require_roles,
)
from app.core.config import get_settings
from app.models.audit import AuditAction, AuditActorType, AuditEntityType
from app.models.user import User, UserRole
from app.schemas.user import (
    UserAvailabilityResponse,
    UserCreate,
    UserRead,
    UserUpdate,
)
from app.services.audit_service import AuditService
from app.services.media_service import ImageValidationError, MediaService
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])

settings = get_settings()


def _sanitize_payload(data: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in {"password", "hashed_password"} and value is not None:
            sanitized[key] = "***"
        else:
            sanitized[key] = value
    return sanitized


@router.post("/", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register_user(
    payload: UserCreate,
    service: UserService = Depends(get_user_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> UserRead:
    try:
        user = await service.register_user(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=user.id,
        action=AuditAction.CREATE,
        entity_type=AuditEntityType.USER,
        entity_id=user.id,
        payload=_sanitize_payload(payload.model_dump()),
        description="User registration",
    )
    return UserRead.model_validate(user)


@router.get("/availability", response_model=UserAvailabilityResponse)
async def check_user_availability(
    username: str | None = None,
    email: str | None = None,
    service: UserService = Depends(get_user_service),
) -> UserAvailabilityResponse:
    if username is None and email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of username or email must be provided",
        )

    response = UserAvailabilityResponse()
    if username is not None:
        response.username_available = await service.is_username_available(username)
    if email is not None:
        response.email_available = await service.is_email_available(email)
    return response


@router.get(
    "/",
    response_model=list[UserRead],
    dependencies=[Depends(require_roles(UserRole.PLAYER))],
)
async def list_users(
    service: UserService = Depends(get_user_service),
) -> list[UserRead]:
    users = await service.list_users()
    return [UserRead.model_validate(user) for user in users]


@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current_user)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(
    user_id: int,
    service: UserService = Depends(get_user_service),
    current_user: User = Depends(get_current_user),
) -> UserRead:
    user = await service.get_user(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if current_user.id != user_id and current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )
    return UserRead.model_validate(user)


@router.put("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int,
    payload: UserUpdate,
    service: UserService = Depends(get_user_service),
    current_user: User = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
) -> UserRead:
    user = await service.get_user(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if current_user.id != user_id and current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )

    try:
        updated = await service.update_user(
            user,
            payload.model_dump(exclude_unset=True),
            actor=current_user,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.USER,
        entity_id=user_id,
        payload=_sanitize_payload(payload.model_dump(exclude_unset=True)),
        description="User profile updated",
    )
    return UserRead.model_validate(updated)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    service: UserService = Depends(get_user_service),
    current_user: User = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
) -> Response:
    user = await service.get_user(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if current_user.id != user_id and current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )

    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.DELETE,
        entity_type=AuditEntityType.USER,
        entity_id=user_id,
        payload={"user_id": user_id, "username": user.username},
        description="User deleted",
    )

    await service.delete_user(user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{user_id}/avatar", response_model=UserRead)
async def upload_user_avatar(
    user_id: int,
    file: UploadFile = File(...),
    service: UserService = Depends(get_user_service),
    current_user: User = Depends(get_current_user),
    media_service: MediaService = Depends(get_media_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> UserRead:
    user = await service.get_user(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if current_user.id != user_id and current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )

    try:
        safe_username = re.sub(r"[^a-zA-Z0-9_-]+", "-", user.username.lower()).strip(
            "-"
        )
        if not safe_username:
            safe_username = f"user-{user_id}"
        target_filename = f"user_{safe_username}.png"
        avatar_url = await media_service.save_image(
            file,
            category="avatars",
            identifier=f"user_{user_id}",
            resize=(settings.image_max_width, settings.image_max_height),
            filename=target_filename,
        )
    except ImageValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    updated = await service.update_user(
        user,
        {"avatar_url": avatar_url},
        actor=current_user,
    )

    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.USER,
        entity_id=user_id,
        payload={"avatar_url": avatar_url},
        description="Updated user avatar",
    )

    return UserRead.model_validate(updated)
