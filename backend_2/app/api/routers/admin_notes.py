from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    get_current_user,
    get_note_service,
    require_roles,
)
from app.models.user import User, UserRole
from app.schemas.note import (
    NoteAdminDetail,
    NoteAdminSummary,
    NoteParticipant,
    NoteUpdate,
)
from app.services.note_service import NoteService

router = APIRouter(
    prefix="/admin/notes",
    tags=["notes"],
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER))],
)


def _to_participant(user: User | None) -> NoteParticipant:
    if user is None:
        return NoteParticipant(id=0, full_name=None, email=None)
    return NoteParticipant(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
    )


def _to_summary(note) -> NoteAdminSummary:
    owner = _to_participant(note.owner)
    shared = [_to_participant(user) for user in note.shared_with]
    return NoteAdminSummary(
        id=note.id,
        title=note.title,
        owner=owner,
        shared_with=shared,
        updated_at=note.updated_at,
        created_at=note.created_at,
        ontology_id=note.ontology_id,
    )


def _to_detail(note) -> NoteAdminDetail:
    summary_data = _to_summary(note).model_dump()
    summary_data["content"] = note.content
    return NoteAdminDetail(**summary_data)


@router.get("/", response_model=list[NoteAdminSummary])
async def list_all_notes(
    service: NoteService = Depends(get_note_service),
) -> list[NoteAdminSummary]:
    notes = await service.list_all()
    return [_to_summary(note) for note in notes]


@router.get("/{note_id}", response_model=NoteAdminDetail)
async def get_note_detail(
    note_id: int,
    service: NoteService = Depends(get_note_service),
) -> NoteAdminDetail:
    note = await service.get(note_id)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found",
        )
    return _to_detail(note)


@router.put("/{note_id}", response_model=NoteAdminDetail)
async def update_note_admin(
    note_id: int,
    payload: NoteUpdate,
    service: NoteService = Depends(get_note_service),
    current_user: User = Depends(get_current_user),
) -> NoteAdminDetail:
    note = await service.get(note_id)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found",
        )
    updated = await service.update_note(
        note,
        title=payload.title,
        content=payload.content,
        ontology_id=payload.ontology_id,
        share_with=payload.share_user_ids,
        actor=current_user,
    )
    return _to_detail(updated)
