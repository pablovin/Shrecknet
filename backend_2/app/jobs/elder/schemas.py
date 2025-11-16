"""Pydantic schemas for Elder job requests and responses."""

from typing import Any, Optional

from pydantic import BaseModel, Field


class RetrievedChunk(BaseModel):
    """Schema for a retrieved context chunk."""

    node_id: str = Field(..., description="Node ID from Neo4j")
    node_label: Optional[str] = Field(None, description="Primary node label")
    node_name: Optional[str] = Field(None, description="Display name for the node")
    node_alias: Optional[str] = Field(None, description="Alias of the node if present")
    instance_id: Optional[str] = Field(
        None, description="Parent ontology instance ID if available"
    )
    chunk_id: Optional[str] = Field(None, description="Chunk identifier")
    chunk_type: Optional[str] = Field(None, description="Type of chunk (text/properties/relationships)")
    chunk_index: Optional[int] = Field(None, description="Chunk ordering index")
    text: str = Field(..., description="Context text from the node")
    score: float = Field(..., description="Similarity score (0-1)")
    confidence_pct: float = Field(..., description="Similarity score expressed as percentage (0-100)")
    source: Optional[str] = Field(None, description="Source identifier")
    properties: dict[str, Any] = Field(
        default_factory=dict, description="Node properties snapshot"
    )


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
    fast: bool = Field(
        False,
        description="Fast mode: single retrieval + single LLM pass (no decompose/validate/style)",
    )
    chat_id: Optional[str] = Field(
        None, description="Optional chat ID to use conversation history as context"
    )
    entities_hint: Optional[str] = Field(
        None,
        description="Optional pre-built list of ontology entities (name + description) to guide decomposition",
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
    retrieval_debug: Optional[list[dict[str, Any]]] = Field(
        None,
        description="Debug information about sub-query retrieval (temporary exposure)",
    )
    trace: Optional[list[TraceStep]] = Field(
        None, description="Execution trace (if include_trace=true)"
    )
