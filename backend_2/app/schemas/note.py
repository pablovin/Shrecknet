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
