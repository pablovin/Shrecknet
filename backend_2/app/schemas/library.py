from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LibraryUserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str


class LibraryItemBase(BaseModel):
    title: str = Field(..., max_length=255)
    description: str | None = None
    cover_url: str | None = Field(None, max_length=512)


class LibraryItemUpdate(BaseModel):
    title: str | None = Field(None, max_length=255)
    description: str | None = None
    cover_url: str | None = Field(None, max_length=512)
    vectorized: bool | None = None
    last_vectorized_at: datetime | None = None


class LibraryItemRead(LibraryItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ontology_id: int
    added_at: datetime
    updated_at: datetime
    vectorized: bool
    last_vectorized_at: datetime | None = None
    pdf_url: str


class LibraryBookmarkBase(BaseModel):
    page: int = Field(..., ge=1)
    title: str = Field(..., max_length=255)
    description: str | None = None
    is_private: bool = True


class LibraryBookmarkCreate(LibraryBookmarkBase):
    shared_user_ids: list[int] = Field(default_factory=list)


class LibraryBookmarkUpdate(BaseModel):
    page: int | None = Field(None, ge=1)
    title: str | None = Field(None, max_length=255)
    description: str | None = None
    is_private: bool | None = None
    shared_user_ids: list[int] | None = None


class LibraryBookmarkRead(LibraryBookmarkBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner: LibraryUserSummary
    created_at: datetime
    updated_at: datetime
    shared_with: list[LibraryUserSummary] = Field(default_factory=list)
