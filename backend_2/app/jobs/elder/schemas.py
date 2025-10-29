"""Pydantic schemas for Elder job requests and responses."""

from typing import Any, Optional

from pydantic import BaseModel, Field


class RetrievedChunk(BaseModel):
    """Schema for a retrieved context chunk."""

    node_id: str = Field(..., description="Node ID from Neo4j")
    node_label: Optional[str] = Field(None, description="Primary node label")
    text: str = Field(..., description="Context text from the node")
    score: float = Field(..., description="Similarity score (0-1)")
    source: Optional[str] = Field(None, description="Source identifier")


class SubAnswer(BaseModel):
    """Schema for a sub-answer to a sub-query."""

    subquery: str = Field(..., description="The sub-query")
    answer: str = Field(..., description="Answer to the sub-query")
    retrieval: list[RetrievedChunk] = Field(
        default_factory=list, description="Retrieved context chunks"
    )


class TraceStep(BaseModel):
    """Schema for a single trace step."""

    step: str = Field(..., description="Step name")
    data: dict[str, Any] = Field(default_factory=dict, description="Step data")


class ElderQueryRequest(BaseModel):
    """Request schema for Elder query."""

    query: str = Field(..., min_length=1, max_length=2000, description="User query")
    mode: str = Field(
        "both",
        pattern="^(nl|context|both)$",
        description="Response mode: 'nl' (natural language), 'context' (context only), or 'both'",
    )
    top_k: Optional[int] = Field(
        None, ge=1, le=50, description="Number of results per sub-query"
    )
    include_trace: bool = Field(False, description="Include execution trace")
    chat_id: Optional[str] = Field(
        None, description="Optional chat ID to use conversation history as context"
    )


class ElderQueryResponse(BaseModel):
    """Response schema for Elder query."""

    agent_id: str = Field(..., description="Agent ID")
    mode: str = Field(..., description="Response mode used")
    query: str = Field(..., description="Original query")
    answer: Optional[str] = Field(
        None, description="Natural language answer (if mode includes 'nl')"
    )
    subanswers: list[SubAnswer] = Field(
        default_factory=list, description="Sub-answers with retrieval context"
    )
    important_nodes: list[str] = Field(
        default_factory=list, description="Top unique node IDs by score"
    )
    context: list[RetrievedChunk] = Field(
        default_factory=list,
        description="Deduplicated context chunks (if mode includes 'context')",
    )
    trace: Optional[list[TraceStep]] = Field(
        None, description="Execution trace (if include_trace=true)"
    )
