from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import Select, and_, select
from sqlalchemy.orm import selectinload

from app.models.note import Note, note_shares
from app.models.user import User
from app.repositories.base import BaseRepository


class NoteRepository(BaseRepository):
    """Persistence helpers for personal and shared notes."""

    async def create(self, data: dict[str, Any], shared_users: Sequence[User]) -> Note:
        note = Note(**data)
        note.shared_with = list(shared_users)
        await self.save(note)
        await self.session.refresh(note)
        return note

    async def get(self, note_id: int) -> Note | None:
        result = await self.session.execute(
            select(Note)
            .options(
                selectinload(Note.shared_with),
                selectinload(Note.owner),
                selectinload(Note.ontology),
            )
            .where(Note.id == note_id)
        )
        return result.scalar_one_or_none()

    async def update(
        self, note: Note, data: dict[str, Any], shared_users: Sequence[User] | None
    ) -> Note:
        for key, value in data.items():
            setattr(note, key, value)
        if shared_users is not None:
            note.shared_with = list(shared_users)
        await self.save(note)
        await self.session.refresh(note)
        return note

    async def delete(self, note: Note) -> None:
        await self.delete_instance(note)

    async def list_owned(self, owner_id: int) -> Sequence[Note]:
        result = await self.session.execute(
            select(Note)
            .options(selectinload(Note.shared_with), selectinload(Note.ontology))
            .where(Note.owner_id == owner_id)
            .order_by(Note.updated_at.desc())
        )
        return result.scalars().unique().all()

    async def list_shared_with(self, user_id: int) -> Sequence[Note]:
        share_alias = note_shares.alias()
        result = await self.session.execute(
            select(Note)
            .join(share_alias, share_alias.c.note_id == Note.id)
            .where(share_alias.c.user_id == user_id)
            .options(
                selectinload(Note.shared_with),
                selectinload(Note.owner),
                selectinload(Note.ontology),
            )
            .order_by(Note.updated_at.desc())
        )
        return result.scalars().unique().all()

    async def ensure_share_targets(self, user_ids: Sequence[int]) -> list[User]:
        if not user_ids:
            return []
        result = await self.session.execute(select(User).where(User.id.in_(user_ids)))
        users = result.scalars().all()
        missing = set(user_ids) - {user.id for user in users}
        if missing:
            raise ValueError(f"Users not found: {sorted(missing)}")
        return list(users)

    async def delete_instance(self, note: Note) -> None:
        self.session.delete(note)
        await self.session.flush()
