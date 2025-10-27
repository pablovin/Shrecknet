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
        await self.session.refresh(note)
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
        await self.session.refresh(updated)

        if new_recipients:
            await self._notify_share(updated, actor, new_recipients)

        return updated

    async def delete_note(self, note: Note) -> None:
        await self.repository.delete(note)
        await self.session.commit()

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
