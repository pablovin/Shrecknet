from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import (
    get_current_user,
    get_note_service,
    require_roles,
)
from app.models.user import User, UserRole
from app.schemas.note import (
    NoteCreate,
    NoteRead,
    NoteShareRequest,
    NoteUpdate,
)
from app.services.note_service import NoteService

router = APIRouter(prefix="/notes", tags=["notes"])


def _serialize_note(note_service: NoteService, note) -> NoteRead:
    return NoteRead(
        id=note.id,
        title=note.title,
        content=note.content,
        ontology_id=note.ontology_id,
        created_at=note.created_at,
        updated_at=note.updated_at,
        owner_id=note.owner_id,
        shared_with=[user.id for user in note.shared_with],
    )


def _ensure_access(note, user: User) -> None:
    if note.owner_id == user.id:
        return
    if any(share.id == user.id for share in note.shared_with):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")


@router.get("/", response_model=list[NoteRead])
async def list_my_notes(
    service: NoteService = Depends(get_note_service),
    current_user: User = Depends(get_current_user),
) -> list[NoteRead]:
    notes = await service.list_owned(current_user)
    return [_serialize_note(service, note) for note in notes]


@router.get("/shared", response_model=list[NoteRead])
async def list_shared_notes(
    service: NoteService = Depends(get_note_service),
    current_user: User = Depends(get_current_user),
) -> list[NoteRead]:
    notes = await service.list_shared_with(current_user)
    return [_serialize_note(service, note) for note in notes]


@router.post("/", response_model=NoteRead, status_code=status.HTTP_201_CREATED)
async def create_note(
    payload: NoteCreate,
    service: NoteService = Depends(get_note_service),
    current_user: User = Depends(get_current_user),
) -> NoteRead:
    note = await service.create_note(
        owner=current_user,
        title=payload.title,
        content=payload.content,
        ontology_id=payload.ontology_id,
        share_with=payload.share_user_ids,
    )
    return _serialize_note(service, note)


@router.get("/{note_id}", response_model=NoteRead)
async def get_note(
    note_id: int,
    service: NoteService = Depends(get_note_service),
    current_user: User = Depends(get_current_user),
) -> NoteRead:
    note = await service.get(note_id)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Note not found"
        )
    _ensure_access(note, current_user)
    return _serialize_note(service, note)


@router.put("/{note_id}", response_model=NoteRead)
async def update_note(
    note_id: int,
    payload: NoteUpdate,
    service: NoteService = Depends(get_note_service),
    current_user: User = Depends(get_current_user),
) -> NoteRead:
    note = await service.get(note_id)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Note not found"
        )
    if note.owner_id != current_user.id and current_user.role not in {
        UserRole.ADMIN,
        UserRole.WORLD_BUILDER,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized"
        )
    updated = await service.update_note(
        note,
        title=payload.title,
        content=payload.content,
        ontology_id=payload.ontology_id,
        share_with=payload.share_user_ids,
        actor=current_user,
    )
    return _serialize_note(service, updated)


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    note_id: int,
    service: NoteService = Depends(get_note_service),
    current_user: User = Depends(get_current_user),
) -> Response:
    note = await service.get(note_id)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Note not found"
        )
    if note.owner_id != current_user.id and current_user.role not in {
        UserRole.ADMIN,
        UserRole.WORLD_BUILDER,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized"
        )
    await service.delete_note(note)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{note_id}/share", response_model=NoteRead)
async def add_shared_users(
    note_id: int,
    payload: NoteShareRequest,
    service: NoteService = Depends(get_note_service),
    current_user: User = Depends(get_current_user),
) -> NoteRead:
    """Add users to a note's share list. Only the note owner can share."""
    note = await service.get(note_id)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Note not found"
        )
    if note.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the note owner can share the note",
        )
    try:
        updated = await service.add_shared_users(note, payload.user_ids, current_user)
        return _serialize_note(service, updated)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/{note_id}/share", response_model=NoteRead)
async def remove_shared_users(
    note_id: int,
    payload: NoteShareRequest,
    service: NoteService = Depends(get_note_service),
    current_user: User = Depends(get_current_user),
) -> NoteRead:
    """Remove users from a note's share list. Only the note owner can unshare."""
    note = await service.get(note_id)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Note not found"
        )
    if note.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the note owner can unshare the note",
        )
    updated = await service.remove_shared_users(note, payload.user_ids)
    return _serialize_note(service, updated)
