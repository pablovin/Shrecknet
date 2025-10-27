from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import Select, desc, func, select
from sqlalchemy.orm import selectinload

from app.models.notification import Notification, NotificationType
from app.repositories.base import BaseRepository


class NotificationRepository(BaseRepository):
    """Persistence helpers for notifications."""

    async def create(self, data: dict[str, Any]) -> Notification:
        notification = Notification(**data)
        await self.save(notification)
        await self.session.refresh(notification)
        return notification

    async def get(self, notification_id: int) -> Notification | None:
        result = await self.session.execute(
            select(Notification).where(Notification.id == notification_id)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        skip: int = 0,
        limit: int = 50,
        user_id: int | None = None,
        read: bool | None = None,
        notification_type: NotificationType | None = None,
    ) -> Sequence[Notification]:
        query: Select[tuple[Notification]] = (
            select(Notification)
            .options(selectinload(Notification.user))
            .order_by(desc(Notification.sent_at), desc(Notification.id))
            .offset(skip)
            .limit(limit)
        )
        if user_id is not None:
            query = query.where(Notification.user_id == user_id)
        if read is not None:
            query = query.where(Notification.read == read)
        if notification_type is not None:
            query = query.where(Notification.notification_type == notification_type)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def update(
        self, notification: Notification, data: dict[str, Any]
    ) -> Notification:
        for key, value in data.items():
            setattr(notification, key, value)
        await self.save(notification)
        await self.session.refresh(notification)
        return notification

    async def remove(self, notification: Notification) -> None:
        await self.delete(notification)

    async def list_for_user(
        self, user_id: int, *, read: bool | None = None
    ) -> Sequence[Notification]:
        query: Select[tuple[Notification]] = (
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(desc(Notification.sent_at), desc(Notification.id))
        )
        if read is not None:
            query = query.where(Notification.read == read)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def count_unread(self, user_id: int) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read.is_(False))
        )
        return int(result.scalar_one())
