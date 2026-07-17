"""Internal schemas for the Elder query and retrieval v2 pipeline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ElderOperation = Literal[
    "resolve_entity",
    "resolve_concept",
    "exact_lookup",
    "hybrid_search",
    "select_nodes",
    "traverse_graph",
    "expand_temporal_context",
    "hydrate_sources",
    "bounded_read_cypher",
]


class QueryIntent(BaseModel):
    kind: Literal["fact", "summary", "relationship", "timeline", "history", "comparison", "mixed"] = "mixed"
    temporal_scope: Literal["none", "latest", "earliest", "before", "after", "so_far", "range", "timeline"] = "none"
    requires_semantic_inference: bool = False
    requires_graph_structure: bool = False


class RetrievalFilters(BaseModel):
    ontology_ids: list[int] = Field(default_factory=list)
    instance_id: str | None = None
    entity_definition_ids: list[int] = Field(default_factory=list)
    source_kinds: list[str] = Field(default_factory=list)
    related_entity_ids: list[str] = Field(default_factory=list)
    derived_from_entity_ids: list[str] = Field(default_factory=list)
    scene_ids: list[str] = Field(default_factory=list)


class TemporalPlan(BaseModel):
    mode: Literal["none", "latest", "earliest", "before", "after", "so_far", "range", "timeline"] = "none"
    anchor: str | None = None
    ordering_priority: list[str] = Field(
        default_factory=lambda: [
            "explicit_relationship", "ontology_property", "domain_date", "created_at"
        ]
    )


class TraversalPlan(BaseModel):
    relationships: list[str] = Field(default_factory=list)
    direction: Literal["outgoing", "incoming", "both"] = "both"
    depth: int = Field(default=0, ge=0, le=2)


class RetrievalStep(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    purpose: str = "Retrieve evidence required by the answer goal"
    operation: ElderOperation
    query: str | None = None
    inputs: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    temporal: TemporalPlan = Field(default_factory=TemporalPlan)
    traversal: TraversalPlan = Field(default_factory=TraversalPlan)
    target_data_type: Literal[
        "entity", "scene", "milestone", "mixed", "ontology_definition"
    ] = "mixed"
    limit: int = Field(default=20, ge=1, le=100)
    hydration_mode: Literal[
        "metadata", "matched_excerpt", "local_context", "complete_source"
    ] = "local_context"
    context_chunks_before: int = Field(default=1, ge=0, le=4)
    context_chunks_after: int = Field(default=1, ge=0, le=4)
    max_tokens_per_source: int = Field(default=1200, ge=128, le=100_000)
    cypher: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    # Read-compatible aliases for plans emitted before the richer v2 contract.
    depends_on: list[str] = Field(default_factory=list, exclude=True)
    source_from: list[str] = Field(default_factory=list, exclude=True)
    traversal_depth: int | None = Field(default=None, ge=1, le=2, exclude=True)

    @model_validator(mode="after")
    def validate_operation_payload(self) -> "RetrievalStep":
        if self.operation == "bounded_read_cypher" and not self.cypher:
            raise ValueError("bounded_read_cypher requires cypher")
        if self.operation != "bounded_read_cypher" and self.cypher:
            raise ValueError("cypher is only valid for bounded_read_cypher")
        return self

    @property
    def dependencies(self) -> set[str]:
        return set(self.inputs) | set(self.depends_on) | set(self.source_from)


class RetrievalPlan(BaseModel):
    answer_goal: str = Field(min_length=1)
    response_scope: Literal["brief", "standard", "deep"] = "standard"
    evidence_budget_tokens: int = Field(default=10_000, ge=1_000, le=200_000)
    query_intent: QueryIntent = Field(default_factory=QueryIntent)
    steps: list[RetrievalStep] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_graph(self) -> "RetrievalPlan":
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("retrieval step ids must be unique")
        known: set[str] = set()
        all_ids = set(ids)
        for step in self.steps:
            missing = step.dependencies - all_ids
            if missing:
                raise ValueError(f"unknown step dependencies: {sorted(missing)}")
            if step.id in step.dependencies:
                raise ValueError("a retrieval step cannot depend on itself")
            # Plans are intentionally topological so execution is deterministic.
            if not step.dependencies.issubset(known):
                raise ValueError("steps must appear after their dependencies")
            known.add(step.id)
        return self


class EvidenceRecord(BaseModel):
    evidence_id: str
    node_id: str
    source_kind: str | None = None
    display_name: str | None = None
    display_text: str
    properties: dict[str, Any] = Field(default_factory=dict)
    associated_entities: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    temporal_position: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0
    retrieval_methods: list[str] = Field(default_factory=list)
    complete: bool = True


class SynthesisEvidence(BaseModel):
    """Compact, identifier-free evidence sent to the synthesis model."""

    evidence_id: str
    source_kind: str | None = None
    source_name: str | None = None
    text: str
    related_entities: list[str] = Field(default_factory=list)
    canonical_facts: dict[str, Any] = Field(default_factory=dict)
    temporal_position: Any = None


class EvidenceCapacityError(RuntimeError):
    """Raised when one complete evidence record cannot fit in a model call."""

    def __init__(self, *, evidence_id: str, required_tokens: int, available_tokens: int):
        self.evidence_id = evidence_id
        self.required_tokens = required_tokens
        self.available_tokens = available_tokens
        super().__init__(
            f"Complete evidence {evidence_id!r} requires {required_tokens} tokens; "
            f"only {available_tokens} are available"
        )
