from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


WebSearchPolicy = Literal["disabled", "fallback", "supplemental"]
AuthorityTier = Literal["house_rules", "core_rules", "supplement", "setting", "errata", "custom"]


class ShreckWorldCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    web_search_enabled: bool = False
    web_search_policy: WebSearchPolicy = "disabled"

    @model_validator(mode="after")
    def validate_web_policy(self) -> "ShreckWorldCreate":
        if not self.web_search_enabled and self.web_search_policy != "disabled":
            raise ValueError("web_search_policy must be disabled while web search is disabled")
        return self


class ShreckWorldUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    web_search_enabled: bool | None = None
    web_search_policy: WebSearchPolicy | None = None


class ShreckWorldRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str | None
    web_search_enabled: bool
    web_search_policy: WebSearchPolicy
    created_at: datetime
    updated_at: datetime


class ShreckWorldLibraryItemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    authors: str | None = Field(default=None, max_length=512)
    description: str | None = None
    authority_tier: AuthorityTier | None = None
    authority_priority: int | None = Field(default=None, ge=0, le=10000)


class ShreckWorldLibraryItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    shreckworld_id: str
    title: str
    authors: str | None
    description: str | None
    authority_tier: AuthorityTier
    authority_priority: int
    embedding_status: str
    embedding_error: str | None
    embedded_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EmbeddingJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    library_item_id: str
    status: str
    progress: int
    detail: str | None
    created_at: datetime
    completed_at: datetime | None


class ShreckWorldQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=6, ge=1, le=20)
    include_trace: bool = False


class ShreckWorldCitation(BaseModel):
    source_id: str
    library_item_id: str
    title: str
    page_number: int
    chunk_id: str
    score: float
    excerpt: str
    authority_tier: AuthorityTier
    authority_priority: int


class ShreckWorldQueryResponse(BaseModel):
    shreckworld_id: str
    query: str
    answer: str
    citations: list[ShreckWorldCitation]
    trace: dict[str, int | str] | None = None
