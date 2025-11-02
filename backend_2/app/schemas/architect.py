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
    chunks: list[str] | None = Field(default=None, description="Text chunks related to this proposal")
    merged_into_proposal_id: str | None = None
    corrected_alias: str | None = None
    corrected_entity_definition_id: int | None = None
    corrected_proposal_type: ArchitectProposalType | None = None
    corrected_entity_instance_id: str | None = None
    generated_entity_instance_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ArchitectAnalysisRunRead(BaseModel):
    """Response model for an architect analysis run."""

    id: str
    agent_id: str | None
    background_job_id: int | None
    generation_job_id: int | None
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
    generation_job_id: int | None
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


class ValidatedProposalItem(BaseModel):
    """A single validated proposal from the client."""

    proposal_id: str
    status: ArchitectProposalStatus
    corrected_alias: str | None = Field(default=None, description="Corrected alias if user modified it")
    corrected_entity_definition_id: int | None = Field(
        default=None, description="Corrected entity definition if user modified it"
    )
    corrected_proposal_type: ArchitectProposalType | None = Field(
        default=None, description="Corrected proposal type (e.g., convert NEW_INSTANCE to UPDATE_INSTANCE)"
    )
    corrected_entity_instance_id: str | None = Field(
        default=None, description="Corrected entity instance ID for UPDATE_INSTANCE proposals"
    )
    merged_into_proposal_id: str | None = Field(
        default=None, description="If merged, the ID of the proposal this was merged into"
    )


class ArchitectValidationRequest(BaseModel):
    """Payload for step 2: processing validated proposals."""

    run_id: str = Field(..., description="The architect run ID from step 1")
    validated_proposals: list[ValidatedProposalItem] = Field(
        ..., min_length=1, description="List of validated proposals"
    )
    author_type: str = Field(default="user", description="Type of author (user or agent)")
    author_id: str = Field(..., description="ID of the author")
