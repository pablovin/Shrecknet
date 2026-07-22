"""Internal, non-public contracts for CharacterAgent query generation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RelevantTrait(StrictModel):
    trait: Literal[
        "calm_aggressive", "cautious_reckless", "compassionate_ruthless",
        "trusting_suspicious", "honest_deceptive", "patient_impulsive",
        "humble_proud", "cooperative_dominating",
    ]
    relevance: int = Field(ge=0, le=100)
    reason: str


class CharacterQueryFrame(StrictModel):
    task_type: str
    task_summary: str
    mandatory_instructions: list[str] = Field(default_factory=list)
    relevant_trait_axes: list[RelevantTrait] = Field(default_factory=list)
    relevant_aspect_ids: list[str] = Field(default_factory=list)
    relevant_goal_ids: list[str] = Field(default_factory=list)
    character_conflicts: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    explicit_options: list[str] = Field(default_factory=list)


class CandidateResponse(StrictModel):
    candidate: str
    goal_alignment: int = Field(ge=0, le=100)
    aspect_alignment: int = Field(ge=0, le=100)
    trait_alignment: int = Field(ge=0, le=100)
    feasibility: int = Field(ge=0, le=100)
    overall_preference: int = Field(ge=0, le=100)
    supporting_ids: list[str] = Field(default_factory=list)


class CharacterDeliberation(StrictModel):
    interpretation: str
    candidate_responses: list[CandidateResponse] = Field(default_factory=list)
    preferred_response: str
    internal_conflict: str | None = None
    decision_basis: list[str] = Field(default_factory=list)
    confidence: int = Field(ge=0, le=100)


class ClaimAssessment(StrictModel):
    claim: str
    classification: Literal[
        "character_fact", "query_fact", "reasonable_inference",
        "creative_expression", "unsupported_claim",
    ]
    supporting_ids: list[str] = Field(default_factory=list)


class VerifiedRendering(StrictModel):
    claim_assessments: list[ClaimAssessment] = Field(default_factory=list)
    unsupported_claims_removed: list[str] = Field(default_factory=list)
    rendered_response: Any
