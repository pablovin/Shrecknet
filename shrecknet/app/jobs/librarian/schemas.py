"""Librarian job schemas for request and response."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LibrarianQueryRequest(BaseModel):
    """Request schema for Librarian query."""

    query: str = Field(..., min_length=1, description="User query")
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
    pdf_url: str | None = None
    page_url: str | None = None
    book_title: str | None = None
    book_authors: str | None = None
    source_id: str | None = None
    chunk_id: str | None = None
    parent_chunk_id: str | None = None
    physical_page_numbers: list[int] = Field(default_factory=list)
    displayed_page_labels: list[str | None] = Field(default_factory=list)
    display_page_label: str | None = None
    bounding_boxes: list[dict[str, Any]] = Field(default_factory=list)
    matched_child_text: str | None = None
    expansion_mode: str | None = None
    incomplete_evidence: bool = False


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
