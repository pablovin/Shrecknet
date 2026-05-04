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
    score_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional override for similarity score threshold",
    )
    candidate_limit: int | None = Field(
        default=None,
        ge=1,
        le=500,
        description="Internal vector candidate pool size before filtering/reranking",
    )
    hybrid_rerank: bool = Field(
        default=True,
        description="Enable hybrid reranking (vector + lexical)",
    )
    max_chunks_per_item: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Optional cap of returned chunks per library item",
    )
    dynamic_score_floor: bool = Field(
        default=False,
        description="Apply a relative score floor based on best candidate",
    )


class RetrievedChunk(BaseModel):
    """Retrieved PDF chunk with metadata."""

    library_item_id: int
    page_number: int
    text: str
    score: float
    pdf_url: str | None = None
    page_url: str | None = None
    book_title: str | None = None
    book_authors: str | None = None


class LibrarianQueryResponse(BaseModel):
    """Response schema for Librarian query."""

    agent_id: str
    mode: str
    query: str
    subqueries: list[str] = Field(default_factory=list)
    answer: str | None = None
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    sources_used: list[RetrievedChunk] = Field(default_factory=list)
    library_items_used: list[int] = Field(default_factory=list)
    trace: list[dict] | None = None
