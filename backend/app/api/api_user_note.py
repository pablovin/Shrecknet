from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from datetime import datetime
from app.database import get_session
from app.dependencies import get_current_user
from app.models.model_user import User
from app.models.model_user_note import UserNote
from app.schemas.schema_user_note import UserNoteCreate, UserNoteRead, UserNoteUpdate
from app.crud.crud_user_note import (
    create_user_note,
    get_user_note,
    get_user_notes,
    update_user_note,
    delete_user_note,
)
from app.task_queue import task_auto_crosslink_note_content

UserNoteRead.model_rebuild()
UserNoteCreate.model_rebuild()
UserNoteUpdate.model_rebuild()

router = APIRouter(prefix="/user_notes", tags=["UserNotes"], dependencies=[Depends(get_current_user)])

@router.post("/", response_model=UserNoteRead)
async def create_note_endpoint(
    note: UserNoteCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    db_note = UserNote(**note.model_dump(), user_id=user.id, created_at=datetime.utcnow())
    db_note = await create_user_note(session, db_note)
    task_auto_crosslink_note_content.delay(db_note.id)
    return db_note

@router.get("/", response_model=List[UserNoteRead])
async def list_notes(
    search: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    notes = await get_user_notes(session, user_id=user.id, search=search, start_date=start_date, end_date=end_date)
    return notes

@router.get("/{note_id}", response_model=UserNoteRead)
async def get_note(note_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    note = await get_user_note(session, note_id)
    if not note or not (note.user_id == user.id or user.id in note.shared_with_user_ids):
        raise HTTPException(status_code=404, detail="Note not found")
    return note

@router.patch("/{note_id}", response_model=UserNoteRead)
async def update_note(note_id: int, updates: UserNoteUpdate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    note = await get_user_note(session, note_id)
    if not note or note.user_id != user.id:
        raise HTTPException(status_code=404, detail="Note not found")
    update_dict = updates.model_dump(exclude_unset=True)
    try:
        note = await update_user_note(session, note_id, update_dict, user.id)
    except PermissionError:
        raise HTTPException(status_code=409, detail="Note is locked for editing")
    task_auto_crosslink_note_content.delay(note.id)
    return note

@router.delete("/{note_id}")
async def delete_note(note_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    note = await get_user_note(session, note_id)
    if not note or note.user_id != user.id:
        raise HTTPException(status_code=404, detail="Note not found")
    await delete_user_note(session, note_id)
    return {"ok": True}
