from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.schemas.query_v3 import EvidenceCoverage, EvidenceGroup, QueryPlan, QueryV3Trace


WebSearchPolicy = Literal["disabled", "fallback", "supplemental"]
AuthorityTier = Literal["house_rules", "core_rules", "supplement", "setting", "errata", "custom"]
ReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh", "max"]


class ShreckWorldModelTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    reasoning: bool = False
    reasoning_effort: ReasoningEffort | None = None

    @field_validator("provider", "name")
    @classmethod
    def nonblank_target_fields(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Model provider and name must not be blank")
        return value.strip()


class ShreckWorldModelSettings(BaseModel):
    model_shreckworld_synthesizer: ShreckWorldModelTarget


class ShreckWorldCreate(BaseModel):
    shrecknet_world_id: str = Field(min_length=1, max_length=64)
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
    shrecknet_world_id: str | None
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
    original_filename: str | None
    mime_type: str | None
    file_size: int | None
    cover_path: str | None
    source_sha256: str
    index_status: str
    index_error: str | None
    embedding_status: str
    embedding_error: str | None
    embedded_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def cover_url(self) -> str | None:
        """Authenticated relative URL for the frontend; never a public media URL."""
        if self.cover_path:
            return f"/shreckworlds/{self.shreckworld_id}/library-items/{self.id}/cover"
        return None


class IndexingJobMetrics(BaseModel):
    phase: Literal["validating_model", "parsing", "chunking", "embedding", "storing", "completed", "failed"]
    progress_mode: Literal["determinate", "indeterminate"] = "determinate"
    parser_name: str | None = None
    parser_version: str | None = None
    parse_quality: dict[str, int | None] = Field(default_factory=dict)
    sections: int = 0
    blocks: int = 0
    parent_chunks: int = 0
    tokenizer_limit: int | None = None
    embedding_fingerprint: str | None = None
    embedding_revision: str | None = None
    processed_pages: int = 0
    total_pages: int | None = None
    text_pages: int = 0
    empty_pages: int = 0
    extracted_characters: int = 0
    processed_chunks: int = 0
    total_chunks: int | None = None
    embedding_backend: Literal["sentence_transformers", "hash"] | None = None
    embedding_model: str | None = None
    warnings: list[str] = Field(default_factory=list)
    phase_seconds: dict[str, float] = Field(default_factory=dict)
    parser_cache_hit: bool | None = None
    parser_seconds: dict[str, float] = Field(default_factory=dict)
    embedding_batch_size: int | None = None


def _utc(value: datetime) -> datetime:
    # SQLite stores naive datetimes even for timezone-aware columns.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class EmbeddingJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    library_item_id: str
    status: str
    progress: int
    detail: str | None
    created_at: datetime
    completed_at: datetime | None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    metrics_json: str | None = Field(default=None, exclude=True)

    @field_validator("created_at", "started_at", "updated_at", "completed_at")
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @computed_field
    @property
    def metrics(self) -> IndexingJobMetrics | None:
        return IndexingJobMetrics.model_validate_json(self.metrics_json) if self.metrics_json else None

    @computed_field
    @property
    def elapsed_seconds(self) -> float:
        metrics = self.metrics
        if self.started_at is not None and metrics and metrics.phase_seconds:
            return round((self.processing_seconds or 0) + (self.queue_wait_seconds or 0), 3)
        end = self.completed_at or datetime.now(timezone.utc)
        return round(max(0, (_utc(end) - _utc(self.created_at)).total_seconds()), 3)

    @computed_field
    @property
    def processing_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        metrics = self.metrics
        if metrics and metrics.phase_seconds:
            # Worker checkpoints use a monotonic clock. Wall-clock adjustments
            # must not rewrite measured parsing/inference durations.
            measured = sum(metrics.phase_seconds.values())
            if self.completed_at is None and self.updated_at is not None:
                measured += max(0, (datetime.now(timezone.utc) - _utc(self.updated_at)).total_seconds())
            return round(measured, 3)
        end = self.completed_at or datetime.now(timezone.utc)
        return round(max(0, (_utc(end) - _utc(self.started_at)).total_seconds()), 3)

    @computed_field
    @property
    def queue_wait_seconds(self) -> float | None:
        if self.started_at is None and self.completed_at is not None:
            return None  # Historical jobs do not have a recorded start time.
        end = self.started_at or datetime.now(timezone.utc)
        return round(max(0, (_utc(end) - _utc(self.created_at)).total_seconds()), 3)


class PassageContext(BaseModel):
    library_item_id: str = Field(min_length=1, max_length=64)
    source_sha256: str = Field(min_length=64, max_length=64)
    page_number: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=4000)


class ShreckWorldQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=6, ge=1, le=20)
    include_trace: bool = False
    library_item_id: str | None = Field(default=None, max_length=64)
    library_item_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    mode: Literal["evidence", "answer"] = "evidence"
    retrieval_strategy: Literal["single", "planned"] = "single"
    selection: PassageContext | None = None

    @field_validator("library_item_ids")
    @classmethod
    def unique_library_item_ids(cls, values: list[str] | None) -> list[str] | None:
        if values is not None and (
            any(not value or len(value) > 64 for value in values)
            or len(set(values)) != len(values)
        ):
            raise ValueError("library_item_ids must contain unique, non-empty IDs of at most 64 characters")
        return values

    @model_validator(mode="after")
    def validate_source_scope(self) -> "ShreckWorldQueryRequest":
        if self.library_item_id is not None and self.library_item_ids is not None:
            raise ValueError("Specify either library_item_id or library_item_ids, not both")
        return self


class PdfBoundingBox(BaseModel):
    left: float | None = None
    top: float | None = None
    right: float | None = None
    bottom: float | None = None
    coord_origin: str | None = None


class CitationProvenance(BaseModel):
    block_id: str
    page_number: int
    page_label: str | None = None
    page_width: float | None = None
    page_height: float | None = None
    bounding_box: PdfBoundingBox | None = None
    charspan: tuple[int, int] | None = None


class CitationRectangle(BaseModel):
    page_number: int = Field(ge=1)
    left: float = Field(ge=0, le=100)
    top: float = Field(ge=0, le=100)
    width: float = Field(gt=0, le=100)
    height: float = Field(gt=0, le=100)


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
    document_id: str | None = None
    parent_chunk_id: str | None = None
    content_type: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    page_numbers: list[int] = Field(default_factory=list)
    evidence_text: str | None = None
    content_url: str | None = None
    provenance: list[CitationProvenance] = Field(default_factory=list)
    source_sha256: str | None = None
    page_label: str | None = None
    parent_excerpt: str | None = None
    rectangles: list[CitationRectangle] = Field(default_factory=list)


class GroundedClaim(BaseModel):
    text: str = Field(min_length=1)
    citation_ids: list[str] = Field(min_length=1)
    kind: Literal["fact", "rule", "condition", "exception", "interpretation"]


class GroundedConflict(BaseModel):
    description: str = Field(min_length=1)
    citation_ids: list[str] = Field(min_length=1)


class QuerySourceCoverage(BaseModel):
    requested_source_count: int = Field(ge=0)
    searched_source_count: int = Field(ge=0)
    sources_without_ready_index: int = Field(ge=0)
    sources_requiring_reindex: int = Field(ge=0)


class ShreckWorldQueryResponse(BaseModel):
    shreckworld_id: str
    query: str
    answer: str
    citations: list[ShreckWorldCitation]
    trace: dict[str, int | str | bool | float] | None = None
    mode: Literal["evidence", "answer"] = "evidence"
    answer_status: Literal["complete", "partial", "unsupported"] | None = None
    claims: list[GroundedClaim] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    conflicts: list[GroundedConflict] = Field(default_factory=list)
    source_coverage: QuerySourceCoverage
    retrieval_strategy: Literal["single", "planned"] = "single"
    query_plan: QueryPlan | None = None
    evidence_coverage: EvidenceCoverage | None = None
    evidence_groups: list[EvidenceGroup] = Field(default_factory=list)
    v3_trace: QueryV3Trace | None = None


class ShreckWorldAgentJobRead(BaseModel):
    id: str
    shreckworld_id: str
    status: Literal["queued", "running", "completed", "failed"]
    stage: str
    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    result: ShreckWorldQueryResponse | None = None
    error: str | None = None

    @field_validator("created_at", "started_at", "updated_at", "completed_at")
    @classmethod
    def normalize_agent_job_timestamps(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None


class ActionResolutionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=2000)
    facts: list[str] = Field(default_factory=list, max_length=20)
    library_item_id: str | None = Field(default=None, max_length=64)

    @field_validator("facts")
    @classmethod
    def bounded_facts(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 500 for value in values):
            raise ValueError("Each fact must contain 1–500 characters")
        return values


class ActionResolutionResponse(BaseModel):
    status: Literal["resolved", "ambiguous", "unsupported"]
    policy_revision: str = "source-priority-v1"
    explanation: str
    citations: list[ShreckWorldCitation]
    preferred_source_ids: list[str] = Field(default_factory=list)
    conflicting_source_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
