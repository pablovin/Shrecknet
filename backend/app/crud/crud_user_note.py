from typing import List, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import or_
from app.models.model_user_note import UserNote


async def create_user_note(session: AsyncSession, note: UserNote) -> UserNote:
    note.locked_by_user_id = None
    note.locked_at = None
    session.add(note)
    await session.commit()
    await session.refresh(note)
    return note


async def get_user_note(session: AsyncSession, note_id: int) -> Optional[UserNote]:
    result = await session.execute(select(UserNote).where(UserNote.id == note_id))
    return result.scalar_one_or_none()


async def get_user_notes(
    session: AsyncSession,
    *,
    user_id: int,
    search: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> List[UserNote]:
    query = select(UserNote)
    if search:
        like = f"%{search}%"
        query = query.where(
            or_(UserNote.title.ilike(like), UserNote.content.ilike(like))
        )
    if start_date:
        query = query.where(UserNote.note_date >= start_date)
    if end_date:
        query = query.where(UserNote.note_date <= end_date)
    result = await session.execute(query)
    notes = result.scalars().all()
    return [
        n
        for n in notes
        if n.user_id == user_id or user_id in (n.shared_with_user_ids or [])
    ]


async def update_user_note(
    session: AsyncSession, note_id: int, updates: dict, editor_id: int
) -> Optional[UserNote]:
    note = await get_user_note(session, note_id)
    if not note:
        return None
    if note.locked_by_user_id and note.locked_by_user_id != editor_id:
        raise PermissionError("Note is being edited by another user")
    for k, v in updates.items():
        setattr(note, k, v)
    if editor_id != note.user_id:
        history = note.contributors or []
        history.append({"user_id": editor_id, "date": datetime.utcnow().isoformat()})
        note.contributors = history
    note.updated_at = datetime.utcnow()
    note.locked_by_user_id = None
    note.locked_at = None
    await session.commit()
    await session.refresh(note)
    return note


async def delete_user_note(session: AsyncSession, note_id: int) -> bool:
    note = await get_user_note(session, note_id)
    if not note:
        return False
    await session.delete(note)
    await session.commit()
    return True
