from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NoteBase(BaseModel):
    title: str = Field(..., max_length=255)
    content: str
    ontology_id: int | None = None


class NoteCreate(NoteBase):
    share_user_ids: list[int] = Field(default_factory=list)


class NoteUpdate(BaseModel):
    title: str | None = Field(None, max_length=255)
    content: str | None = None
    ontology_id: int | None = None
    share_user_ids: list[int] | None = None


class NoteRead(NoteBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
    owner_id: int
    shared_with: list[int] = Field(default_factory=list)


class NoteShareRequest(BaseModel):
    user_ids: list[int] = Field(..., min_length=1)


class NoteParticipant(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str | None = None
    email: str | None = None


class NoteAdminSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    owner: NoteParticipant
    shared_with: list[NoteParticipant] = Field(default_factory=list)
    updated_at: datetime
    created_at: datetime
    ontology_id: int | None = None


class NoteAdminDetail(NoteAdminSummary):
    content: str
