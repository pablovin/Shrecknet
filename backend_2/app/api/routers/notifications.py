from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import (
    get_audit_service,
    get_current_user,
    get_notification_service,
    require_roles,
)
from app.models.audit import AuditAction, AuditActorType, AuditEntityType
from app.models.notification import Notification, NotificationType
from app.models.user import User, UserRole
from app.schemas.notification import (
    NotificationCreate,
    NotificationRead,
    NotificationReadState,
    NotificationUnreadCount,
    NotificationUpdate,
)
from app.services.audit_service import AuditService
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _sanitize_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


@router.post(
    "/", response_model=NotificationRead, status_code=status.HTTP_201_CREATED,
)
async def create_notification(
    payload: NotificationCreate,
    service: NotificationService = Depends(get_notification_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> NotificationRead:
    create_data = payload.model_dump(exclude_none=True)
    notification = await service.create_notification(create_data)
    await _log_notification_action(
        audit_service,
        actor=current_user,
        action=AuditAction.CREATE,
        notification=notification,
        payload=create_data,
        description="Created notification",
    )
    return NotificationRead.model_validate(notification)


@router.get(
    "/",
    response_model=list[NotificationRead],
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER))],
)
async def list_notifications(
    skip: int = 0,
    limit: int = 50,
    user_id: int | None = None,
    read: bool | None = None,
    notification_type: NotificationType | None = None,
    service: NotificationService = Depends(get_notification_service),
) -> list[NotificationRead]:
    notifications = await service.list_notifications(
        skip=skip,
        limit=limit,
        user_id=user_id,
        read=read,
        notification_type=notification_type,
    )
    return [NotificationRead.model_validate(item) for item in notifications]


@router.get(
    "/me", response_model=list[NotificationRead],
)
async def list_my_notifications(
    read: bool | None = None,
    service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
) -> list[NotificationRead]:
    notifications = await service.list_user_notifications(current_user.id, read=read)
    return [NotificationRead.model_validate(item) for item in notifications]


@router.get(
    "/me/unread-count", response_model=NotificationUnreadCount,
)
async def unread_count(
    service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
) -> NotificationUnreadCount:
    count = await service.unread_count(current_user.id)
    return NotificationUnreadCount(unread_count=count)


async def _get_notification_or_404(
    notification_id: int, service: NotificationService,
) -> Notification:
    notification = await service.get_notification(notification_id)
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )
    return notification


@router.get(
    "/{notification_id}", response_model=NotificationRead,
)
async def get_notification(
    notification_id: int,
    service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> NotificationRead:
    notification = await _get_notification_or_404(notification_id, service)
    return NotificationRead.model_validate(notification)


@router.put(
    "/{notification_id}", response_model=NotificationRead,
)
async def update_notification(
    notification_id: int,
    payload: NotificationUpdate,
    service: NotificationService = Depends(get_notification_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> NotificationRead:
    notification = await _get_notification_or_404(notification_id, service)
    update_data = payload.model_dump(exclude_unset=True, exclude_none=True)
    updated = await service.update_notification(notification, update_data)
    await _log_notification_action(
        audit_service,
        actor=current_user,
        action=AuditAction.UPDATE,
        notification=updated,
        payload=_sanitize_payload(update_data),
        description="Updated notification",
    )
    return NotificationRead.model_validate(updated)


@router.delete(
    "/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER))],
)
async def delete_notification(
    notification_id: int,
    service: NotificationService = Depends(get_notification_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> Response:
    notification = await _get_notification_or_404(notification_id, service)
    await service.delete_notification(notification)
    await _log_notification_action(
        audit_service,
        actor=current_user,
        action=AuditAction.DELETE,
        notification=notification,
        payload={"notification_id": notification_id, "user_id": notification.user_id},
        description="Deleted notification",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{notification_id}/read", response_model=NotificationRead,
)
async def set_read_state(
    notification_id: int,
    payload: NotificationReadState,
    service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
) -> NotificationRead:
    notification = await _get_notification_or_404(notification_id, service)
    if (
        current_user.role not in {UserRole.ADMIN, UserRole.WORLD_BUILDER}
        and notification.user_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )
    updated = await service.mark_notification_read(notification, read=payload.read)
    return NotificationRead.model_validate(updated)


async def _log_notification_action(
    audit_service: AuditService,
    *,
    actor: User,
    action: AuditAction,
    notification: Notification,
    payload: dict[str, Any],
    description: str,
) -> None:
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=actor.id,
        action=action,
        entity_type=AuditEntityType.NOTIFICATION,
        entity_id=notification.id,
        payload=_sanitize_payload(payload),
        description=description,
    )
