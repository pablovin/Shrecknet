from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.architect import (
    ArchitectProposalStatus,
    ArchitectProposalType,
    ArchitectRunStatus,
)


class ArchitectAnalysisRequest(BaseModel):
    """Payload for requesting an architect analysis run."""

    ontology_instance_id: str = Field(
        ..., min_length=1, description="Target instance id"
    )
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
        ge=100,
        le=3000,
        description="Override chunk size in words (default: 1000 words).",
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
    chunks: list[str] | None = Field(
        default=None, description="Text chunks related to this proposal"
    )
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
    corrected_alias: str | None = Field(
        default=None, description="Corrected alias if user modified it"
    )
    corrected_entity_definition_id: int | None = Field(
        default=None, description="Corrected entity definition if user modified it"
    )
    corrected_proposal_type: ArchitectProposalType | None = Field(
        default=None,
        description="Corrected proposal type (e.g., convert NEW_INSTANCE to UPDATE_INSTANCE)",
    )
    corrected_entity_instance_id: str | None = Field(
        default=None,
        description="Corrected entity instance ID for UPDATE_INSTANCE proposals",
    )
    merged_into_proposal_id: str | None = Field(
        default=None,
        description="If merged, the ID of the proposal this was merged into",
    )


class RevisedSuggestion(BaseModel):
    """Revised suggestion coming from the frontend after user curation."""

    suggestion_id: str = Field(..., description="Original proposal identifier")
    action: Literal["new", "updated", "merged"] = Field(
        ..., description="How the suggestion should be applied"
    )
    alias: str | None = Field(
        default=None, description="Alias after user edits or merges"
    )
    entity_definition_id: int | None = Field(
        default=None, description="Final entity definition chosen by the user"
    )
    entity_instance_id: str | None = Field(
        default=None, description="Target entity when action=updated"
    )
    chunk_indices: list[int] | None = Field(
        default=None,
        description="Relevant chunk indices for this suggestion (optional)",
    )
    merged_suggestion_ids: list[str] | None = Field(
        default=None,
        description="When merging, the proposal ids that were merged together",
    )
    merged_aliases: list[str] | None = Field(
        default=None,
        description="All aliases the user merged for this suggestion",
    )
    status: ArchitectProposalStatus | None = Field(
        default=None,
        description="Frontend approval status; only approved/merged should be generated",
    )

class ArchitectValidationRequest(BaseModel):
    """Payload for step 2: processing validated proposals."""

    run_id: str = Field(..., description="The architect run ID from step 1")
    validated_proposals: list[ValidatedProposalItem] | None = Field(
        default=None, description="List of validated proposals (v1 compatibility)"
    )
    revised_suggestions: list[RevisedSuggestion] | None = Field(
        default=None,
        description="Curated suggestions coming from the frontend (v2 preferred payload)",
    )
    author_type: str = Field(
        default="user", description="Type of author (user or agent)"
    )
    author_id: str = Field(..., description="ID of the author")

    @model_validator(mode="after")
    def ensure_payload(cls, values: "ArchitectValidationRequest") -> "ArchitectValidationRequest":
        if not values.validated_proposals and not values.revised_suggestions:
            raise ValueError(
                "Either validated_proposals or revised_suggestions must be provided"
            )
        return values


FrontendProposalStatus = Literal[
    "approved",
    "approved_with_updates",
    "disapproved",
]


class FrontendEntityProposalUpdates(BaseModel):
    name: str | None = None
    corrected_entity_definition_id: int | None = None
    corrected_entity_instance_id: str | None = None
    ontology: str | None = None


class FrontendEntityProposal(BaseModel):
    name: str
    canonical: str | None = None
    ontology: str | None = None
    status: FrontendProposalStatus
    proposal_type: ArchitectProposalType | str | None = None
    entity_instance_id: str | None = None
    scene_refs: list[str] = Field(default_factory=list)
    updates: FrontendEntityProposalUpdates | None = None


class FrontendRelatedEntityRef(BaseModel):
    canonical: str | None = None
    alias: str | None = None
    entity_instance_id: str | None = None
    proposal_index: int | None = None
    proposal_type: str | None = None
    status: str | None = None


class FrontendSceneOrderRef(BaseModel):
    scene_ref: str | None = None
    scene_id: str | None = None


class FrontendSceneProposal(BaseModel):
    scene_ref: str
    scene_id: str | None = None
    scene_order: int | None = None
    scene_name: str
    scene_description: str
    scene_text: str
    source_entity_instance_id: str | None = None
    related_to: list[FrontendRelatedEntityRef] = Field(default_factory=list)
    preceded_by: FrontendSceneOrderRef | None = None
    followed_by: FrontendSceneOrderRef | None = None
    status: FrontendProposalStatus


class FrontendMilestoneRelation(BaseModel):
    entity: str
    relationship_label: str
    relationship_description: str | None = None


class FrontendMilestoneProposal(BaseModel):
    milestone_ref: str
    scene_ref: str | None = None
    scene_id: str | None = None
    title: str | None = None
    label: str | None = None
    description: str
    boundary_type: Literal["begin", "end", "none"] = "none"
    milestone_order: int | None = None
    related_to: list[FrontendMilestoneRelation] = Field(default_factory=list)
    status: FrontendProposalStatus


class FrontendMilestonesPerSceneItem(BaseModel):
    scene_ref: str
    scene_id: str | None = None
    milestones: list[FrontendMilestoneProposal] = Field(default_factory=list)


class ArchitectReviewedPipelineOutput(BaseModel):
    run_id: str
    ontology_instance_id: str
    outputs: dict[str, Any]

    @property
    def entity_proposals(self) -> list[FrontendEntityProposal]:
        proposals = self.outputs.get("entity_proposals") or []
        return [FrontendEntityProposal.model_validate(item) for item in proposals]

    @property
    def scene_proposals(self) -> list[FrontendSceneProposal]:
        proposals = self.outputs.get("scene_proposals") or []
        return [FrontendSceneProposal.model_validate(item) for item in proposals]

    @property
    def milestone_proposals(self) -> list[FrontendMilestoneProposal]:
        proposals = self.outputs.get("milestone_proposals") or []
        return [FrontendMilestoneProposal.model_validate(item) for item in proposals]

    @property
    def milestones_per_scene(self) -> list[FrontendMilestonesPerSceneItem]:
        payload = self.outputs.get("milestones_per_scene") or []
        return [FrontendMilestonesPerSceneItem.model_validate(item) for item in payload]


class ArchitectGenerationRequest(BaseModel):
    run_id: str
    reviewed_pipeline_output: ArchitectReviewedPipelineOutput
    author_type: str = Field(default="user")
    author_id: str

    @model_validator(mode="after")
    def ensure_run_matches(cls, values: "ArchitectGenerationRequest") -> "ArchitectGenerationRequest":
        if values.run_id != values.reviewed_pipeline_output.run_id:
            raise ValueError("run_id must match reviewed_pipeline_output.run_id")
        return values
