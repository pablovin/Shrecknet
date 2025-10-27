from __future__ import annotations

from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import (
    Notification,
    NotificationAuthorType,
    NotificationType,
)
from app.repositories.notification_repository import NotificationRepository


class NotificationService:
    """Business logic for managing notifications."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = NotificationRepository(session)

    async def create_notification(self, data: dict) -> Notification:
        data = self._normalize_payload(data)
        notification = await self.repository.create(data)
        await self.session.commit()
        return notification

    async def list_notifications(
        self,
        *,
        skip: int = 0,
        limit: int = 50,
        user_id: int | None = None,
        read: bool | None = None,
        notification_type: NotificationType | None = None,
    ) -> Sequence[Notification]:
        notifications = await self.repository.list(
            skip=skip,
            limit=limit,
            user_id=user_id,
            read=read,
            notification_type=notification_type,
        )
        return notifications

    async def get_notification(self, notification_id: int) -> Notification | None:
        return await self.repository.get(notification_id)

    async def update_notification(
        self, notification: Notification, data: dict
    ) -> Notification:
        if "notification_type" in data and data["notification_type"] is not None:
            data["notification_type"] = NotificationType(data["notification_type"])
        if "author_type" in data and data["author_type"] is not None:
            data["author_type"] = NotificationAuthorType(data["author_type"])
        updated = await self.repository.update(notification, data)
        await self.session.commit()
        return updated

    async def delete_notification(self, notification: Notification) -> None:
        await self.repository.remove(notification)
        await self.session.commit()

    async def list_user_notifications(
        self, user_id: int, *, read: bool | None = None
    ) -> Sequence[Notification]:
        notifications = await self.repository.list_for_user(user_id, read=read)
        return notifications

    async def mark_notification_read(
        self, notification: Notification, *, read: bool = True
    ) -> Notification:
        notification.read = read
        await self.repository.save(notification)
        await self.session.commit()
        await self.session.refresh(notification)
        return notification

    async def unread_count(self, user_id: int) -> int:
        return await self.repository.count_unread(user_id)

    def _normalize_payload(self, data: dict) -> dict:
        data = data.copy()
        data["notification_type"] = NotificationType(data["notification_type"])
        data["author_type"] = NotificationAuthorType(data["author_type"])
        if data.get("sent_at") is None:
            data.pop("sent_at", None)
        if data.get("sent_date") is None:
            data.pop("sent_date", None)
        if "send_email" not in data:
            data["send_email"] = False
        return data
