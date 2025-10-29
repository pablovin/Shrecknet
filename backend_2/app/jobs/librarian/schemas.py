"""Librarian job schemas for request and response."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LibrarianQueryRequest(BaseModel):
    """Request schema for Librarian query."""

    query: str = Field(..., min_length=1, max_length=2000, description="User query")
    mode: Literal["nl", "context", "both"] = Field(
        default="both",
        description="Response mode: 'nl' for natural language only, "
        "'context' for context only, 'both' for answer and context",
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Number of chunks to retrieve (default from config)",
    )
    library_item_ids: list[int] | None = Field(
        default=None,
        description="Optional list of library item IDs to search within",
    )
    include_trace: bool = Field(
        default=False, description="Include execution trace for debugging"
    )


class RetrievedChunk(BaseModel):
    """Retrieved PDF chunk with metadata."""

    library_item_id: int
    page_number: int
    text: str
    score: float


class LibrarianQueryResponse(BaseModel):
    """Response schema for Librarian query."""

    agent_id: str
    mode: str
    query: str
    answer: str | None = None
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    library_items_used: list[int] = Field(default_factory=list)
    trace: list[dict] | None = None
