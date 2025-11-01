from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.architect import (
    ArchitectProposalStatus,
    ArchitectProposalType,
    ArchitectRunStatus,
)


class ArchitectAnalysisRequest(BaseModel):
    """Payload for requesting an architect analysis run."""

    ontology_instance_id: str = Field(..., min_length=1, description="Target instance id")
    ontology_id: int | None = Field(
        default=None,
        description="Optional ontology id override when the instance id is not unique",
    )
    max_chunks: int | None = Field(
        default=None,
        ge=1,
        le=200,
        description="Optional maximum number of text chunks to analyse",
    )
    chunk_size: int | None = Field(
        default=None,
        ge=128,
        le=4096,
        description="Override chunk size in characters before tokenisation.",
    )


class ArchitectProposalRead(BaseModel):
    """Response model for a single proposal item."""

    id: str
    proposal_type: ArchitectProposalType
    status: ArchitectProposalStatus
    entity_definition_id: int | None
    entity_instance_id: str | None
    alias: str | None
    confidence: float | None
    justification: str | None
    evidence: list[dict[str, Any]] | None
    metadata: dict[str, Any] | None = Field(
        default=None,
        validation_alias="proposal_metadata",
        serialization_alias="metadata",
    )
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ArchitectAnalysisRunRead(BaseModel):
    """Response model for an architect analysis run."""

    id: str
    agent_id: str | None
    background_job_id: int | None
    ontology_id: int | None
    ontology_instance_id: str
    status: ArchitectRunStatus
    input_chunk_count: int | None
    settings: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    proposals: list[ArchitectProposalRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ArchitectAnalysisRunSummary(BaseModel):
    """Lightweight summary for listing runs."""

    id: str
    agent_id: str | None
    background_job_id: int | None
    ontology_id: int | None
    ontology_instance_id: str
    status: ArchitectRunStatus
    input_chunk_count: int | None
    created_at: datetime
    updated_at: datetime


class ArchitectProposalStatusUpdate(BaseModel):
    """Payload to update the status of a proposal."""

    proposal_ids: list[str] = Field(..., min_length=1)
    status: ArchitectProposalStatus = Field(...)
