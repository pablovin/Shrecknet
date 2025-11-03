from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from datetime import datetime
from app.database import get_session
from app.dependencies import get_current_user, require_role
from app.models.model_user import User, UserRole
from app.models.model_user_note import UserNote
from app.schemas.schema_user_note import UserNoteCreate, UserNoteRead, UserNoteUpdate, AdminUserNoteCreate
from app.crud.crud_user_note import (
    create_user_note,
    get_user_note,
    get_user_notes,
    update_user_note,
    delete_user_note,
)
from app.crud.crud_users import get_user

try:
    from app.task_queue import task_auto_crosslink_note_content
except Exception:  # pragma: no cover - optional task queue
    task_auto_crosslink_note_content = None

UserNoteRead.model_rebuild()
UserNoteCreate.model_rebuild()
UserNoteUpdate.model_rebuild()
AdminUserNoteCreate.model_rebuild()

router = APIRouter(
    prefix="/user_notes", tags=["UserNotes"], dependencies=[Depends(get_current_user)]
)


@router.post("/", response_model=UserNoteRead)
async def create_note_endpoint(
    note: UserNoteCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    db_note = UserNote(
        **note.model_dump(), user_id=user.id, created_at=datetime.utcnow()
    )
    db_note = await create_user_note(session, db_note)
    if task_auto_crosslink_note_content:
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
    notes = await get_user_notes(
        session,
        user_id=user.id,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )
    return notes


@router.get("/{note_id}", response_model=UserNoteRead)
async def get_note(
    note_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    note = await get_user_note(session, note_id)
    if not note or not (
        note.user_id == user.id or user.id in note.shared_with_user_ids
    ):
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@router.patch("/{note_id}", response_model=UserNoteRead)
async def update_note(
    note_id: int,
    updates: UserNoteUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    note = await get_user_note(session, note_id)
    if not note or not (
        note.user_id == user.id or user.id in note.shared_with_user_ids
    ):
        raise HTTPException(status_code=404, detail="Note not found")
    update_dict = updates.model_dump(exclude_unset=True)
    if note.user_id != user.id and "shared_with_user_ids" in update_dict:
        update_dict.pop("shared_with_user_ids")
    try:
        note = await update_user_note(session, note_id, update_dict, user.id)
    except PermissionError:
        raise HTTPException(status_code=409, detail="Note is locked for editing")
    if task_auto_crosslink_note_content:
        task_auto_crosslink_note_content.delay(note.id)
    return note


@router.delete("/{note_id}")
async def delete_note(
    note_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    note = await get_user_note(session, note_id)
    if not note or note.user_id != user.id:
        raise HTTPException(status_code=404, detail="Note not found")
    await delete_user_note(session, note_id)
    return {"ok": True}


# Admin router for note administration
admin_router = APIRouter(
    prefix="/admin/user_notes",
    tags=["AdminUserNotes"],
    dependencies=[Depends(require_role(UserRole.system_admin))],
)


@admin_router.post("/", response_model=UserNoteRead)
async def admin_create_note(
    note: AdminUserNoteCreate,
    admin_user: User = Depends(require_role(UserRole.system_admin)),
    session: AsyncSession = Depends(get_session),
):
    """
    Admin endpoint to create a note on behalf of a user.
    The admin specifies the author_user_id and can also specify shared_with_user_ids.
    """
    # Verify that the author_user_id exists
    author = await get_user(session, note.author_user_id)
    if not author:
        raise HTTPException(status_code=404, detail=f"Author user with id {note.author_user_id} not found")
    
    # Verify that all shared_with_user_ids exist
    for shared_user_id in note.shared_with_user_ids:
        shared_user = await get_user(session, shared_user_id)
        if not shared_user:
            raise HTTPException(status_code=404, detail=f"Shared user with id {shared_user_id} not found")
    
    # Create the note with the specified author
    note_data = note.model_dump(exclude={"author_user_id"})
    db_note = UserNote(
        **note_data,
        user_id=note.author_user_id,
        created_at=datetime.utcnow()
    )
    db_note = await create_user_note(session, db_note)
    
    if task_auto_crosslink_note_content:
        task_auto_crosslink_note_content.delay(db_note.id)
    
    return db_note
