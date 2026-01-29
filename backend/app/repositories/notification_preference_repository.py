from __future__ import annotations

from sqlalchemy import Select, select

from app.models.notification import NotificationType
from app.models.notification_preference import NotificationPreference
from app.repositories.base import BaseRepository


class NotificationPreferenceRepository(BaseRepository):
    """Persistence helpers for notification preferences."""

    async def create(self, data: dict) -> NotificationPreference:
        preference = NotificationPreference(**data)
        await self.save(preference)
        await self.session.refresh(preference)
        return preference

    async def get_for_user_type(
        self, user_id: int, notification_type: NotificationType
    ) -> NotificationPreference | None:
        result = await self.session.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.notification_type == notification_type,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: int) -> list[NotificationPreference]:
        query: Select[tuple[NotificationPreference]] = select(
            NotificationPreference
        ).where(NotificationPreference.user_id == user_id)
        result = await self.session.execute(query)
        return list(result.scalars().all())
