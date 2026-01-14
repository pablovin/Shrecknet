from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import NotificationAuthorType, NotificationType
from app.repositories.favorite_ontology_instance_repository import (
    FavoriteOntologyInstanceRepository,
)
from app.repositories.notification_repository import NotificationRepository

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


async def notify_favorite_instance_update(
    session: AsyncSession,
    instance_id: str,
    instance_name: str,
    ontology_id: int,
    update_type: str,
    update_details: str,
    author_id: str | None = None,
) -> None:
    """
    Notify all users who favorited an instance about an update.

    Args:
        session: SQLAlchemy async session
        instance_id: ID of the ontology instance that was updated
        instance_name: Name of the instance for the notification
        ontology_id: ID of the ontology
        update_type: Type of update (e.g., "content", "timeline", "properties")
        update_details: Details about what changed
        author_id: ID of the author who made the change (optional, defaults to None for system)
    """
    favorite_repo = FavoriteOntologyInstanceRepository(session)
    notification_repo = NotificationRepository(session)

    # Get all users who favorited this instance
    user_ids = await favorite_repo.get_users_who_favorited(instance_id)

    if not user_ids:
        logger.debug(f"No users have favorited instance {instance_id}")
        return

    logger.info(
        f"Sending favorite instance update notifications to {len(user_ids)} users for instance {instance_id}"
    )

    # Create notifications for each user
    for user_id in user_ids:
        try:
            notification_data = {
                "user_id": user_id,
                "notification_type": NotificationType.FAVORITE_INSTANCE_UPDATE,
                "title": f"Update to favorited item: {instance_name}",
                "description": f"{update_type}: {update_details}",
                "author_type": NotificationAuthorType.USER,
                "author_id": author_id or "system",
                "read": False,
                "send_email": False,
            }
            await notification_repo.create(notification_data)
        except Exception as e:
            logger.error(
                f"Failed to create notification for user {user_id}: {e}",
                exc_info=True,
            )

    await session.flush()
    logger.info(
        f"Created {len(user_ids)} notifications for instance {instance_id} update"
    )
