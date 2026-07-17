"""Pydantic schemas for Elder job requests and responses."""

from __future__ import annotations

from typing import Any, Literal, Optional

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
    chunk_type: Optional[str] = Field(None, description="Type of chunk")
    chunk_index: Optional[int] = Field(None, description="Chunk ordering index")
    text: str = Field(..., description="Context text from the node")
    score: float = Field(..., description="Similarity score (0-1)")
    confidence_pct: float = Field(..., description="Similarity score as percentage")
    source: Optional[str] = Field(None, description="Source identifier")
    properties: dict[str, Any] = Field(
        default_factory=dict, description="Node properties snapshot"
    )
    chunk_score: Optional[float] = Field(
        None, description="Best chunk-level score for this node"
    )
    node_score: Optional[float] = Field(
        None, description="Deterministic node-level reranked score"
    )
    importance_index: Optional[float] = Field(
        None, description="Final importance score used for ordering"
    )
    matched_chunk_count: Optional[int] = Field(
        None, description="How many chunk candidates matched this node"
    )
    score_breakdown: Optional[dict[str, float]] = Field(
        None, description="Deterministic scoring signal breakdown"
    )
    graph_boost: Optional[float] = Field(
        None, description="Bounded graph-aware additive boost"
    )
    evidence_bundle: Optional[dict[str, Any]] = Field(
        None, description="Structured additive evidence bundle for this node"
    )


class TraceStep(BaseModel):
    """Schema for a single trace step."""

    step: str = Field(..., description="Step name")
    data: dict[str, Any] = Field(default_factory=dict, description="Step data")


class SourceEvidenceChunk(BaseModel):
    """Evidence chunk attached to a source node."""

    chunk_id: Optional[str] = None
    chunk_type: Optional[str] = None
    score: float = 0.0
    text: Optional[str] = None
    complete: bool = True


class SourceNode(BaseModel):
    """Grounding source returned to clients."""

    node_id: str
    node_label: Optional[str] = None
    node_name: Optional[str] = None
    node_type: Optional[str] = None
    scene_id: Optional[str] = None
    source_entity_instance_id: Optional[str] = None
    score: float = 0.0
    evidence_chunks: list[SourceEvidenceChunk] = Field(default_factory=list)
    evidence_id: Optional[str] = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    temporal_position: dict[str, Any] = Field(default_factory=dict)
    retrieval_methods: list[str] = Field(default_factory=list)
    complete: bool = True
    canonical_text: Optional[str] = None
    properties: dict[str, Any] = Field(default_factory=dict)


class ElderRetrievalPlanStep(BaseModel):
    """Frontend-safe v2 retrieval step without execution-only scope or identifiers."""

    id: str
    purpose: str
    operation: str
    query: Optional[str] = None
    inputs: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    temporal: dict[str, Any] = Field(default_factory=dict)
    traversal: dict[str, Any] = Field(default_factory=dict)
    target_data_type: str
    limit: int
    hydration_mode: str
    context_chunks_before: int
    context_chunks_after: int
    max_tokens_per_source: int


class ElderRetrievalPlan(BaseModel):
    """Stable public projection of the active v2 retrieval plan."""

    answer_goal: str
    response_scope: Literal["brief", "standard", "deep"]
    evidence_budget_tokens: int
    query_intent: dict[str, Any] = Field(default_factory=dict)
    steps: list[ElderRetrievalPlanStep] = Field(default_factory=list)


class ElderQueryRequest(BaseModel):
    """Request schema for Elder query."""

    query: str = Field(..., min_length=1, description="User query")
    # Kept for request backward-compatibility; response no longer depends on mode.
    mode: str = Field(
        "both",
        pattern="^(nl|context|both)$",
        description="Legacy response mode field",
    )
    top_k: Optional[int] = Field(
        None, ge=1, le=50, description="Number of results per intent"
    )
    include_trace: bool = Field(False, description="Include execution trace")
    fast: bool = Field(
        False,
        description="Fast mode: skip decomposition and use one mixed intent",
    )
    route: Literal["auto", "fast", "deep"] = Field(
        "auto",
        description="Execution route: auto (fast-first), fast (single-pass), deep (always decompose before retrieval)",
    )
    chat_id: Optional[str] = Field(
        None, description="Optional chat ID to use conversation history as context"
    )
    instance_id: Optional[str] = Field(
        None, description="Optional active ontology instance used to scope retrieval"
    )
    entities_hint: Optional[str] = Field(
        None,
        description="Optional pre-built list of ontology entities (name + description)",
    )
    grounding_definitions: Optional[list[dict[str, Any]]] = Field(
        None,
        description="Internal structured ontology definitions supplied by the API layer",
        exclude=True,
    )
    node_scope: Literal["everything", "entity", "scene", "milestone", "mixed"] = Field(
        "everything",
        description="Legacy retrieval scope. Decomposition target_data_type is preferred.",
    )
    candidate_limit: Optional[int] = Field(
        120,
        ge=5,
        le=200,
        description="Max chunk candidates before node-level reranking",
    )
    rerank_limit: Optional[int] = Field(
        50,
        ge=1,
        le=100,
        description="Max node candidates after reranking before final top-k",
    )


class ElderQueryResponse(BaseModel):
    """Source-grounded Elder response contract."""

    agent_id: str = Field(..., description="Agent ID")
    query: str = Field(..., description="Original query")
    answer: str = Field(..., description="Grounded synthesized answer")
    timings: dict[str, float] = Field(default_factory=dict)
    retrieval_plan: ElderRetrievalPlan
    sources: list[SourceNode] = Field(default_factory=list)
    memory_priors_applied: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: str = Field(..., description="Trace identifier for logs")
    trace: Optional[list[TraceStep]] = Field(
        None, description="Execution trace when include_trace=true"
    )
    retrieval_debug: Optional[list[dict[str, Any]]] = Field(
        None,
        description="Debug information about v2 retrieval steps and counters",
    )
    pipeline_version: Literal["elder-query-retrieval-v2"] = "elder-query-retrieval-v2"
    llm_usage: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-LLM-call token usage in execution order",
    )
    llm_usage_totals: dict[str, int] = Field(
        default_factory=dict,
        description="Total Elder LLM input, output, and combined tokens",
    )
