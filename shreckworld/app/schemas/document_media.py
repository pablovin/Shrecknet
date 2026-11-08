"""Bounded source-media discovery; complete citation references may have extra keys."""
from typing import Literal
from pydantic import BaseModel, Field


class MediaReference(BaseModel):
    library_item_id: str = Field(min_length=1, max_length=64)
    document_id: str = Field(min_length=1, max_length=64)
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    page_number: int = Field(ge=1, le=100000, strict=True)


class MediaRequest(BaseModel):
    references: list[MediaReference] = Field(min_length=1, max_length=4)


class MediaRead(BaseModel):
    id: str
    kind: Literal['page_thumbnail', 'book_illustration']
    document_id: str
    source_sha256: str
    page_number: int
    width: int
    height: int
    bounds: list[float] = Field(min_length=4, max_length=4)


class MediaList(BaseModel):
    items: list[MediaRead] = Field(max_length=16)
