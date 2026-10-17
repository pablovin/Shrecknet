"""Typed source reading, semantic retrieval, and indexing-job access for ShreckWorld."""
from datetime import datetime
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, Field

from .client import AsyncShrecknetClient


class KnowledgeWorld(BaseModel):
    id: str
    shrecknet_world_id: str | None = None
    name: str
    description: str | None = None


class KnowledgeSource(BaseModel):
    id: str
    shreckworld_id: str
    title: str
    authors: str | None = None
    description: str | None = None
    cover_url: str | None = None
    file_size: int | None = None
    mime_type: str | None = None
    source_sha256: str
    index_status: str


class SelectedPassage(BaseModel):
    library_item_id: str
    source_sha256: str = Field(min_length=64, max_length=64)
    page_number: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=4000)


class KnowledgeBoundingBox(BaseModel):
    left: float | None = None
    top: float | None = None
    right: float | None = None
    bottom: float | None = None
    coord_origin: str | None = None


class KnowledgeProvenance(BaseModel):
    block_id: str
    page_number: int
    page_label: str | None = None
    page_width: float | None = None
    page_height: float | None = None
    bounding_box: KnowledgeBoundingBox | None = None
    charspan: tuple[int, int] | None = None


class KnowledgeRectangle(BaseModel):
    page_number: int
    left: float
    top: float
    width: float
    height: float


class KnowledgeCitation(BaseModel):
    source_id: str
    library_item_id: str
    title: str
    page_number: int
    chunk_id: str
    excerpt: str
    source_sha256: str | None = None
    document_id: str | None = None
    page_label: str | None = None
    parent_excerpt: str | None = None
    authority_priority: int
    authority_tier: str | None = None
    score: float | None = None
    parent_chunk_id: str | None = None
    content_type: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    page_numbers: list[int] = Field(default_factory=list)
    evidence_text: str | None = None
    content_url: str | None = None
    provenance: list[KnowledgeProvenance] = Field(default_factory=list)
    rectangles: list[KnowledgeRectangle] = Field(default_factory=list)


class KnowledgeAnswer(BaseModel):
    shreckworld_id: str
    query: str
    answer: str
    citations: list[KnowledgeCitation]
    trace: dict[str, int | str | float | bool] | None = None


class RuleReview(BaseModel):
    status: Literal['resolved', 'ambiguous', 'unsupported']
    policy_revision: str
    explanation: str
    citations: list[KnowledgeCitation]
    preferred_source_ids: list[str] = Field(default_factory=list)
    conflicting_source_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)


class KnowledgeJobMetrics(BaseModel):
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


class KnowledgeIndexingJob(BaseModel):
    id: str
    library_item_id: str
    status: Literal["queued", "running", "completed", "failed"]
    progress: int
    detail: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    elapsed_seconds: float | None = None
    processing_seconds: float | None = None
    queue_wait_seconds: float | None = None
    metrics: KnowledgeJobMetrics | None = None


class KnowledgeEmbeddingHealth(BaseModel):
    ready: bool
    backend: str
    model: str
    fingerprint: str
    revision: str | None = None
    dimension: int
    device: str


class KnowledgeReadiness(BaseModel):
    status: Literal["ready"]
    embedding: KnowledgeEmbeddingHealth
    parser: str
    parser_artifacts_present: bool


class ShreckWorldAPI:
    """Shares Shrecknet identity; querying/resolution never mutates game state."""
    def __init__(self, identity: AsyncShrecknetClient, base_url: str = 'http://localhost:8121', timeout: float = 30):
        self._identity = identity
        self._transport = AsyncShrecknetClient(base_url=base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def _request(self, method: str, path: str, **kwargs):
        # Read the current token for every request, including after login/refresh.
        self._transport.token = self._identity.token
        return await self._transport.raw_request(method, path, **kwargs)

    async def worlds(self) -> list[KnowledgeWorld]:
        return [KnowledgeWorld.model_validate(value) for value in await self._request('GET', '/shreckworlds')]

    async def sources(self, world_id: str) -> list[KnowledgeSource]:
        values = await self._request('GET', f'/shreckworlds/{quote(world_id, safe="")}/library-items')
        return [KnowledgeSource.model_validate(value) for value in values]

    async def query(self, world_id: str, question: str, *, library_item_id: str | None = None,
                    selection: SelectedPassage | None = None, top_k: int = 6, include_trace: bool = False) -> KnowledgeAnswer:
        payload = {'query': question, 'top_k': top_k, 'include_trace': include_trace}
        if library_item_id is not None:
            payload['library_item_id'] = library_item_id
        if selection is not None:
            payload['selection'] = selection.model_dump()
        value = await self._request('POST', f'/shreckworlds/{quote(world_id, safe="")}/query', json=payload)
        return KnowledgeAnswer.model_validate(value)

    async def resolve_action(self, world_id: str, action: str, *, facts: list[str] | None = None,
                             library_item_id: str | None = None) -> RuleReview:
        payload = {'action': action, 'facts': facts or []}
        if library_item_id is not None:
            payload['library_item_id'] = library_item_id
        value = await self._request('POST', f'/shreckworlds/{quote(world_id, safe="")}/resolve-action', json=payload)
        return RuleReview.model_validate(value)

    async def readiness(self) -> KnowledgeReadiness:
        """Check the actual local semantic encoder; unavailable models raise 503."""
        return KnowledgeReadiness.model_validate(await self._request("GET", "/ready"))

    async def start_indexing(self, world_id: str, item_id: str) -> KnowledgeIndexingJob:
        """Start a structured semantic generation (admin/world_builder token)."""
        path = f'/admin/shreckworlds/{quote(world_id, safe="")}/library-items/{quote(item_id, safe="")}/index'
        return KnowledgeIndexingJob.model_validate(await self._request("POST", path))

    async def indexing_job(self, world_id: str, item_id: str, *, job_id: str | None = None) -> KnowledgeIndexingJob:
        """Poll a specific attempt, or the latest attempt when job_id is omitted."""
        suffix = f'index-jobs/{quote(job_id, safe="")}' if job_id is not None else 'index-status'
        path = f'/admin/shreckworlds/{quote(world_id, safe="")}/library-items/{quote(item_id, safe="")}/{suffix}'
        return KnowledgeIndexingJob.model_validate(await self._request("GET", path))
