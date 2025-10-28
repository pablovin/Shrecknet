from __future__ import annotations

from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.note import Note
from app.models.notification import NotificationAuthorType, NotificationType
from app.models.user import User
from app.repositories.note_repository import NoteRepository
from app.services.notification_service import NotificationService


class NoteService:
    """Business logic for personal and shared notes."""

    def __init__(
        self, session: AsyncSession, notification_service: NotificationService
    ) -> None:
        self.session = session
        self.repository = NoteRepository(session)
        self.notification_service = notification_service

    async def create_note(
        self,
        *,
        owner: User,
        title: str,
        content: str,
        ontology_id: int | None,
        share_with: Sequence[int],
    ) -> Note:
        share_ids = [user_id for user_id in share_with if user_id != owner.id]
        share_users = await self.repository.ensure_share_targets(share_ids)
        note = await self.repository.create(
            {
                "owner_id": owner.id,
                "ontology_id": ontology_id,
                "title": title,
                "content": content,
            },
            share_users,
        )
        await self.session.commit()
        # Note is already eagerly loaded by repository.create
        await self._notify_share(note, owner, share_users)
        return note

    async def list_owned(self, owner: User) -> list[Note]:
        return list(await self.repository.list_owned(owner.id))

    async def list_shared_with(self, user: User) -> list[Note]:
        return list(await self.repository.list_shared_with(user.id))

    async def get(self, note_id: int) -> Note | None:
        return await self.repository.get(note_id)

    async def update_note(
        self,
        note: Note,
        *,
        title: str | None,
        content: str | None,
        ontology_id: int | None,
        share_with: Sequence[int] | None,
        actor: User,
    ) -> Note:
        data: dict[str, object | None] = {}
        if title is not None:
            data["title"] = title
        if content is not None:
            data["content"] = content
        if ontology_id is not None:
            data["ontology_id"] = ontology_id

        share_users: Sequence[User] | None = None
        new_recipients: list[User] = []
        if share_with is not None:
            filtered_ids = [
                user_id for user_id in share_with if user_id != note.owner_id
            ]
            share_users = await self.repository.ensure_share_targets(filtered_ids)
            existing_ids = {user.id for user in note.shared_with}
            new_recipients = [
                user for user in share_users if user.id not in existing_ids
            ]

        updated = await self.repository.update(note, data, share_users)
        await self.session.commit()
        # Note is already eagerly loaded by repository.update

        if new_recipients:
            await self._notify_share(updated, actor, new_recipients)

        return updated

    async def delete_note(self, note: Note) -> None:
        await self.repository.delete(note)
        await self.session.commit()

    async def add_shared_users(
        self, note: Note, user_ids: Sequence[int], actor: User
    ) -> Note:
        """Add users to a note's share list."""
        # Filter out owner and duplicates
        filtered_ids = [
            user_id
            for user_id in user_ids
            if user_id != note.owner_id
            and user_id not in {u.id for u in note.shared_with}
        ]
        if not filtered_ids:
            return note

        new_users = await self.repository.ensure_share_targets(filtered_ids)
        await self.repository.add_shared_users(note, new_users)
        await self.session.commit()
        # Re-fetch with eager loading to avoid lazy load issues
        refreshed_note = await self.repository.get(note.id)
        await self._notify_share(refreshed_note, actor, new_users)  # type: ignore[arg-type]
        return refreshed_note  # type: ignore[return-value]

    async def remove_shared_users(self, note: Note, user_ids: Sequence[int]) -> Note:
        """Remove users from a note's share list."""
        # Filter to only users currently in the share list
        users_to_remove = [user for user in note.shared_with if user.id in user_ids]
        if not users_to_remove:
            return note

        await self.repository.remove_shared_users(note, users_to_remove)
        await self.session.commit()
        # Re-fetch with eager loading to avoid lazy load issues
        return await self.repository.get(note.id)  # type: ignore[return-value]

    async def _notify_share(
        self, note: Note, actor: User, shared_users: Sequence[User]
    ) -> None:
        if not shared_users:
            return
        for user in shared_users:
            if user.id == actor.id:
                continue
            await self.notification_service.create_notification(
                {
                    "user_id": user.id,
                    "notification_type": NotificationType.NOTE_UPDATES.value,
                    "title": f"Note shared: {note.title}",
                    "description": f"{actor.full_name} shared a note with you.",
                    "author_type": NotificationAuthorType.USER.value,
                    "author_id": str(actor.id),
                    "send_email": False,
                }
            )
