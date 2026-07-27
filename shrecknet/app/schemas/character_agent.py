"""Schemas for ontology-scoped CharacterAgent graph administration."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.models.character_embodiment import CharacterEmbodimentDraftStatus


class CharacterAgentStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class CharacterAgentVisibility(str, Enum):
    PRIVATE = "private"
    PUBLIC = "public"


class CharacterAspectCategory(str, Enum):
    IDENTITY = "identity"
    ROLE = "role"
    STATUS = "status"
    PHYSICAL = "physical"
    CAPABILITY = "capability"
    KNOWLEDGE = "knowledge"
    PREFERENCE = "preference"
    ATTITUDE = "attitude"
    HISTORY = "history"


class CharacterAspectStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class CharacterGoalType(str, Enum):
    DESIRE = "desire"
    OBJECTIVE = "objective"
    AMBITION = "ambition"
    OBLIGATION = "obligation"
    AVOIDANCE = "avoidance"
    SURVIVAL = "survival"


class CharacterGoalStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    SUPERSEDED = "superseded"


class ScenePerspectiveSourceType(str, Enum):
    PARTICIPATED = "participated"
    WITNESSED = "witnessed"
    HEARD_ABOUT = "heard_about"
    READ_ABOUT = "read_about"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class ScenePerspectiveStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"


class CharacterBeliefStatus(str, Enum):
    SUSPECTED = "suspected"
    BELIEVED = "believed"
    CONFIRMED = "confirmed"
    DOUBTED = "doubted"
    DISPROVEN = "disproven"
    SUPERSEDED = "superseded"


class CharacterImpactType(str, Enum):
    GOAL_CHANGE = "goal_change"
    ASPECT_CHANGE = "aspect_change"


class CharacterImpactDirection(str, Enum):
    ADVANCED = "advanced"
    THREATENED = "threatened"
    CREATED = "created"
    REINFORCED = "reinforced"
    INVALIDATED = "invalidated"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _evidence_ids(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    return [str(item) for item in value] if isinstance(value, list) else []


class CharacterAgentCreate(_StrictModel):
    ontology_id: int = Field(..., ge=1)
    entity_instance_id: str = Field(..., min_length=1)
    name: str | None = Field(None, min_length=1, max_length=255)
    subtitle: str | None = Field(None, min_length=1, max_length=255)
    background_story: str | None = Field(None, min_length=1)
    image_url: str | None = Field(None, max_length=2048)
    status: CharacterAgentStatus = CharacterAgentStatus.ACTIVE
    visibility: CharacterAgentVisibility = CharacterAgentVisibility.PRIVATE
    calm_aggressive: int = Field(50, ge=0, le=100)
    cautious_reckless: int = Field(50, ge=0, le=100)
    compassionate_ruthless: int = Field(50, ge=0, le=100)
    trusting_suspicious: int = Field(50, ge=0, le=100)
    honest_deceptive: int = Field(50, ge=0, le=100)
    patient_impulsive: int = Field(50, ge=0, le=100)
    humble_proud: int = Field(50, ge=0, le=100)
    cooperative_dominating: int = Field(50, ge=0, le=100)
    trait_adherence: int = Field(80, ge=0, le=100)

    @field_validator("entity_instance_id")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("name", "subtitle", "background_story")
    @classmethod
    def strip_optional_default(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class CharacterAgentUpdate(_StrictModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    subtitle: str | None = Field(None, min_length=1, max_length=255)
    background_story: str | None = Field(None, min_length=1)
    image_url: str | None = Field(None, max_length=2048)
    status: CharacterAgentStatus | None = None
    visibility: CharacterAgentVisibility | None = None
    calm_aggressive: int | None = Field(None, ge=0, le=100)
    cautious_reckless: int | None = Field(None, ge=0, le=100)
    compassionate_ruthless: int | None = Field(None, ge=0, le=100)
    trusting_suspicious: int | None = Field(None, ge=0, le=100)
    honest_deceptive: int | None = Field(None, ge=0, le=100)
    patient_impulsive: int | None = Field(None, ge=0, le=100)
    humble_proud: int | None = Field(None, ge=0, le=100)
    cooperative_dominating: int | None = Field(None, ge=0, le=100)
    trait_adherence: int | None = Field(None, ge=0, le=100)

    @field_validator("name", "subtitle", "background_story")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class CharacterAgentRead(CharacterAgentCreate):
    id: str
    name: str
    background_story: str
    embodied_entity_instance_id: str
    embodiment_draft_id: str | None = None
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime


class CharacterEmbodimentCandidate(_StrictModel):
    entity_instance_id: str
    ontology_id: int
    entity_definition_id: int
    entity_type_name: str
    entity_type_image_url: str | None = None
    name: str
    background_story: str
    avatar_url: str | None = None
    image_url: str | None = None


class CharacterEmbodimentCandidatePage(_StrictModel):
    total: int
    skip: int
    limit: int
    results: list[CharacterEmbodimentCandidate]


BEHAVIOURAL_AXES = (
    "calm_aggressive", "cautious_reckless", "compassionate_ruthless",
    "trusting_suspicious", "honest_deceptive", "patient_impulsive",
    "humble_proud", "cooperative_dominating",
)


class EmbodimentEvidence(_StrictModel):
    evidence_id: str
    kind: Literal["identity", "property", "relationship", "scene", "milestone", "semantic_document"]
    text: str
    source_id: str
    occurred_at: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class EmbodimentGroundedStatement(_StrictModel):
    text: str = Field(..., min_length=1)
    evidence_ids: list[str] = Field(..., min_length=1)


class EmbodimentEvidenceGap(_StrictModel):
    text: str = Field(..., min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class EmbodimentObservations(_StrictModel):
    identity_description: EmbodimentGroundedStatement
    recurring_behaviours: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    important_experiences: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    motivations: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    values: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    fears: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    conflicts: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    relationships: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    possible_goals: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    possible_aspects: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    contradictions: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    evidence_gaps: list[EmbodimentEvidenceGap] = Field(default_factory=list)


class EmbodimentAxisProposal(_StrictModel):
    axis: Literal[
        "calm_aggressive", "cautious_reckless", "compassionate_ruthless",
        "trusting_suspicious", "honest_deceptive", "patient_impulsive",
        "humble_proud", "cooperative_dominating",
    ]
    value: int = Field(..., ge=0, le=100)
    justification: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0, le=1)
    evidence_ids: list[str] = Field(..., min_length=1)


class EmbodimentAspectProposal(_StrictModel):
    suggestion_id: str
    name: str = Field(..., min_length=1, max_length=255)
    category: CharacterAspectCategory
    description: str | None = None
    importance: int = Field(..., ge=1, le=5)
    intensity: int | None = Field(None, ge=0, le=100)
    justification: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0, le=1)
    evidence_ids: list[str] = Field(..., min_length=1)


class EmbodimentGoalProposal(_StrictModel):
    suggestion_id: str
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    goal_type: CharacterGoalType
    status: CharacterGoalStatus = CharacterGoalStatus.ACTIVE
    priority: int = Field(..., ge=0, le=100)
    commitment: int = Field(..., ge=0, le=100)
    justification: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0, le=1)
    evidence_ids: list[str] = Field(..., min_length=1)
    basis: Literal["explicit", "inferred"]


class EmbodimentAxesProposal(_StrictModel):
    behavioural_axes: list[EmbodimentAxisProposal] = Field(
        ..., min_length=8, max_length=8
    )


class EmbodimentAspectsProposal(_StrictModel):
    aspects: list[EmbodimentAspectProposal] = Field(default_factory=list)


class EmbodimentGoalsProposal(_StrictModel):
    goals: list[EmbodimentGoalProposal] = Field(default_factory=list)


class EmbodimentProposal(_StrictModel):
    name: str = Field(..., min_length=1, max_length=255)
    subtitle: str | None = Field(None, max_length=255)
    background_story: str = Field(..., min_length=1)
    image_url: str | None = Field(None, max_length=2048)
    status: CharacterAgentStatus = CharacterAgentStatus.ACTIVE
    visibility: CharacterAgentVisibility = CharacterAgentVisibility.PRIVATE
    trait_adherence: int = Field(80, ge=0, le=100)
    behavioural_axes: list[EmbodimentAxisProposal] = Field(..., min_length=8, max_length=8)
    aspects: list[EmbodimentAspectProposal] = Field(default_factory=list)
    goals: list[EmbodimentGoalProposal] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_suggestion_ids(self):
        identifiers = [
            item.suggestion_id for item in [*self.aspects, *self.goals]
        ]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("aspect and goal suggestion IDs must be unique")
        return self


class CharacterAgentEmbeddedAspect(_StrictModel):
    suggestion_id: str | None = None
    name: str = Field(..., min_length=1, max_length=255)
    category: CharacterAspectCategory
    description: str | None = None
    importance: int = Field(..., ge=1, le=5)
    intensity: int | None = Field(None, ge=0, le=100)
    justification: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float | None = Field(None, ge=0, le=1)


class CharacterAgentEmbeddedGoal(_StrictModel):
    suggestion_id: str | None = None
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    goal_type: CharacterGoalType
    status: CharacterGoalStatus = CharacterGoalStatus.ACTIVE
    priority: int = Field(50, ge=0, le=100)
    commitment: int = Field(50, ge=0, le=100)
    justification: str | None = None
    basis: Literal["explicit", "inferred"] | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float | None = Field(None, ge=0, le=1)


class CharacterAgentCreateRequest(CharacterAgentCreate):
    embodiment_draft_id: str | None = Field(None, min_length=1)
    aspects: list[CharacterAgentEmbeddedAspect] = Field(default_factory=list)
    goals: list[CharacterAgentEmbeddedGoal] = Field(default_factory=list)


class EmbodimentDraftCreate(_StrictModel):
    ontology_id: int = Field(..., ge=1)
    entity_instance_id: str = Field(..., min_length=1)


class EmbodimentDraftStart(_StrictModel):
    draft_id: str
    job_id: int
    status: CharacterEmbodimentDraftStatus
    draft_url: str
    job_url: str


class EmbodimentDraftRead(_StrictModel):
    id: str
    ontology_id: int
    source_entity_id: str
    target_character_agent_id: str | None = None
    status: CharacterEmbodimentDraftStatus
    background_job_id: int | None = None
    generation_revision: int
    evidence: list[EmbodimentEvidence] = Field(default_factory=list)
    source_evidence_ids: list[str] = Field(default_factory=list)
    evidence_cutoff: str | None = None
    observations: EmbodimentObservations | None = None
    proposal: EmbodimentProposal | None = None
    timeline: CharacterTimelineProjection | None = None
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    error_message: str | None = None
    generated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime




class CharacterAspectCreate(_StrictModel):
    ontology_id: int = Field(..., ge=1)
    name: str = Field(..., min_length=1, max_length=255)
    category: CharacterAspectCategory
    description: str | None = None
    status: CharacterAspectStatus = CharacterAspectStatus.ACTIVE
    obtained_from_scene_id: str | None = None

    @field_validator("name")
    @classmethod
    def strip_aspect_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class CharacterAspectUpdate(_StrictModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    category: CharacterAspectCategory | None = None
    description: str | None = None
    status: CharacterAspectStatus | None = None
    obtained_from_scene_id: str | None = None

    @field_validator("name")
    @classmethod
    def strip_aspect_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class CharacterAspectRead(_StrictModel):
    id: str
    ontology_id: int
    name: str
    normalized_name: str
    category: CharacterAspectCategory
    description: str | None = None
    status: CharacterAspectStatus
    justification: str | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    generated_by_embodiment_draft_id: str | None = None
    obtained_from_scene_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def parse_evidence_ids(cls, value: Any) -> list[str]:
        return _evidence_ids(value)


class CharacterAspectAssignmentCreate(_StrictModel):
    character_aspect_id: str
    importance: int = Field(..., ge=1, le=5)
    intensity: int | None = Field(None, ge=0, le=100)
    notes: str | None = None
    status: CharacterAspectStatus = CharacterAspectStatus.ACTIVE


class CharacterAspectAssignmentUpdate(_StrictModel):
    importance: int | None = Field(None, ge=1, le=5)
    intensity: int | None = Field(None, ge=0, le=100)
    notes: str | None = None
    status: CharacterAspectStatus | None = None


class CharacterAspectAssignmentRead(_StrictModel):
    aspect: CharacterAspectRead
    importance: int
    intensity: int | None = None
    notes: str | None = None
    status: CharacterAspectStatus
    justification: str | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def parse_evidence_ids(cls, value: Any) -> list[str]:
        return _evidence_ids(value)


class CharacterGoalCreate(_StrictModel):
    ontology_id: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    goal_type: CharacterGoalType
    status: CharacterGoalStatus = CharacterGoalStatus.ACTIVE
    priority: int = Field(50, ge=0, le=100)
    commitment: int = Field(50, ge=0, le=100)
    obtained_from_scene_id: str | None = None

    @field_validator("title")
    @classmethod
    def strip_goal_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class CharacterGoalUpdate(_StrictModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    goal_type: CharacterGoalType | None = None
    status: CharacterGoalStatus | None = None
    priority: int | None = Field(None, ge=0, le=100)
    commitment: int | None = Field(None, ge=0, le=100)
    obtained_from_scene_id: str | None = None

    @field_validator("title")
    @classmethod
    def strip_goal_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class CharacterGoalRead(_StrictModel):
    id: str
    ontology_id: int
    title: str
    description: str | None = None
    goal_type: CharacterGoalType
    status: CharacterGoalStatus
    priority: int
    commitment: int
    justification: str | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    basis: Literal["explicit", "inferred"] | None = None
    generated_by_embodiment_draft_id: str | None = None
    obtained_from_scene_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def parse_evidence_ids(cls, value: Any) -> list[str]:
        return _evidence_ids(value)


class _NarrativeFields(_StrictModel):
    @field_validator("description", "summary", "interpretation", "statement", check_fields=False)
    @classmethod
    def strip_narrative(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class EmotionalInterpretationCreate(_NarrativeFields):
    arousal: int = Field(..., ge=0, le=100)
    valence: int = Field(..., ge=0, le=100)
    description: str = Field(..., min_length=1)


class EmotionalInterpretationUpdate(_NarrativeFields):
    arousal: int | None = Field(None, ge=0, le=100)
    valence: int | None = Field(None, ge=0, le=100)
    description: str | None = Field(None, min_length=1)


class EmotionalInterpretationRead(EmotionalInterpretationCreate):
    id: str
    ontology_id: int
    created_at: datetime
    updated_at: datetime


class CharacterBeliefCreate(_NarrativeFields):
    statement: str = Field(..., min_length=1)
    confidence: int = Field(..., ge=0, le=100)
    status: CharacterBeliefStatus


class CharacterBeliefUpdate(_NarrativeFields):
    statement: str | None = Field(None, min_length=1)
    confidence: int | None = Field(None, ge=0, le=100)
    status: CharacterBeliefStatus | None = None


class CharacterBeliefRead(CharacterBeliefCreate):
    id: str
    ontology_id: int
    created_at: datetime
    updated_at: datetime


class CharacterImpactCreate(_NarrativeFields):
    impact_type: CharacterImpactType
    direction: CharacterImpactDirection
    magnitude: int = Field(..., ge=0, le=100)
    description: str = Field(..., min_length=1)
    target_id: str = Field(..., min_length=1)
    caused_by_milestone_id: str | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def valid_direction(self):
        goal_directions = {
            CharacterImpactDirection.ADVANCED,
            CharacterImpactDirection.THREATENED,
        }
        aspect_directions = {
            CharacterImpactDirection.CREATED,
            CharacterImpactDirection.REINFORCED,
            CharacterImpactDirection.INVALIDATED,
        }
        permitted = (
            goal_directions
            if self.impact_type == CharacterImpactType.GOAL_CHANGE
            else aspect_directions
        )
        if self.direction not in permitted:
            raise ValueError("impact direction is incompatible with impact_type")
        return self


class CharacterImpactUpdate(_NarrativeFields):
    direction: CharacterImpactDirection | None = None
    magnitude: int | None = Field(None, ge=0, le=100)
    description: str | None = Field(None, min_length=1)
    caused_by_milestone_id: str | None = Field(None, min_length=1)


class CharacterImpactRead(_StrictModel):
    id: str
    ontology_id: int
    impact_type: CharacterImpactType
    direction: CharacterImpactDirection
    magnitude: int
    description: str
    target_id: str
    target_type: Literal["goal", "aspect"]
    caused_by_milestone_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ScenePerspectiveCreate(_NarrativeFields):
    scene_id: str = Field(..., min_length=1)
    source_type: ScenePerspectiveSourceType
    awareness_level: int = Field(..., ge=0, le=100)
    confidence: int = Field(..., ge=0, le=100)
    summary: str = Field(..., min_length=1)
    interpretation: str = Field(..., min_length=1)
    memory_strength: int = Field(..., ge=0, le=100)
    importance: int = Field(..., ge=1, le=5)
    status: ScenePerspectiveStatus = ScenePerspectiveStatus.ACTIVE


class ScenePerspectiveUpdate(_NarrativeFields):
    source_type: ScenePerspectiveSourceType | None = None
    awareness_level: int | None = Field(None, ge=0, le=100)
    confidence: int | None = Field(None, ge=0, le=100)
    summary: str | None = Field(None, min_length=1)
    interpretation: str | None = Field(None, min_length=1)
    memory_strength: int | None = Field(None, ge=0, le=100)
    importance: int | None = Field(None, ge=1, le=5)
    status: ScenePerspectiveStatus | None = None


class ScenePerspectiveRead(_StrictModel):
    id: str
    ontology_id: int
    character_agent_id: str
    scene_id: str
    generated_with_revision_id: str | None = None
    source_group_id: str | None = None
    source_type: ScenePerspectiveSourceType
    awareness_level: int
    confidence: int
    summary: str
    interpretation: str
    memory_strength: int
    importance: int
    status: ScenePerspectiveStatus
    created_at: datetime
    updated_at: datetime


class ScenePerspectiveAggregateRead(ScenePerspectiveRead):
    emotions: list[EmotionalInterpretationRead] = Field(default_factory=list)
    beliefs: list[CharacterBeliefRead] = Field(default_factory=list)
    impacts: list[CharacterImpactRead] = Field(default_factory=list)


class SourceSceneInput(_StrictModel):
    scene_id: str
    title: str
    description: str
    created_at: str | None = None
    entity_relation: dict[str, Any] = Field(default_factory=dict)


class CharacterSourceGroup(_StrictModel):
    source_group_id: str
    source_name: str
    source_created_at: str | None = None
    scenes: list[SourceSceneInput] = Field(default_factory=list)


class ProjectedScenePerspective(_StrictModel):
    scene_id: str
    source_type: ScenePerspectiveSourceType
    awareness_level: int = Field(..., ge=0, le=100)
    confidence: int = Field(..., ge=0, le=100)
    summary: str = Field(..., min_length=1)
    interpretation: str = Field(..., min_length=1)
    memory_strength: int = Field(..., ge=0, le=100)
    importance: int = Field(..., ge=1, le=5)
    status: ScenePerspectiveStatus = ScenePerspectiveStatus.ACTIVE


class SourcePerspectiveProjection(_StrictModel):
    perspectives: list[ProjectedScenePerspective] = Field(default_factory=list)


class SubtitleChangeProposal(_StrictModel):
    operation: Literal["retain", "set", "clear"] = "retain"
    subtitle: str | None = Field(None, max_length=255)
    justification: str | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_operation(self):
        if self.operation == "set" and not (self.subtitle or "").strip():
            raise ValueError("set subtitle operation requires subtitle")
        if self.operation != "set" and self.subtitle is not None:
            raise ValueError("subtitle is only valid for set operation")
        if self.operation != "retain" and (
            not self.justification or self.confidence is None or not self.evidence_ids
        ):
            raise ValueError("subtitle changes require justification, confidence and evidence")
        return self


class SourceAspectsConsolidation(EmbodimentAspectsProposal):
    subtitle_change: SubtitleChangeProposal = Field(default_factory=SubtitleChangeProposal)


class CharacterIdentityRevisionProjection(_StrictModel):
    revision_number: int = Field(..., ge=0)
    source_group_id: str | None = None
    last_processed_scene_id: str | None = None
    name: str
    subtitle: str | None = None
    trait_adherence: int = Field(..., ge=0, le=100)
    behavioural_axes: dict[str, int]
    active_aspects: list[EmbodimentAspectProposal] = Field(default_factory=list)
    active_goals: list[EmbodimentGoalProposal] = Field(default_factory=list)


class CharacterSourceProjection(_StrictModel):
    source_group_id: str
    starting_revision_number: int = Field(..., ge=0)
    perspectives: list[ProjectedScenePerspective]
    axis_changes: list[EmbodimentAxisProposal] = Field(default_factory=list)
    aspects: list[EmbodimentAspectProposal] = Field(default_factory=list)
    goals: list[EmbodimentGoalProposal] = Field(default_factory=list)
    subtitle_change: SubtitleChangeProposal = Field(default_factory=SubtitleChangeProposal)
    resulting_revision: CharacterIdentityRevisionProjection


class CharacterTimelineProjection(_StrictModel):
    revisions: list[CharacterIdentityRevisionProjection]
    source_projections: list[CharacterSourceProjection] = Field(default_factory=list)


class CharacterIdentityRevisionRead(_StrictModel):
    id: str
    character_agent_id: str
    revision_number: int
    source_group_id: str | None = None
    last_processed_scene_id: str | None = None
    name: str
    subtitle: str | None = None
    trait_adherence: int
    behavioural_axes: dict[str, int]
    active_aspect_ids: list[str] = Field(default_factory=list)
    active_goal_ids: list[str] = Field(default_factory=list)
    provenance_type: Literal["generated", "manual", "initial"]
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    created_at: datetime

    @field_validator("behavioural_axes", "active_aspect_ids", "active_goal_ids", mode="before")
    @classmethod
    def parse_revision_json(cls, value: Any):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return {} if value.lstrip().startswith("{") else []
        return value


class CharacterIdentityChangeRead(_StrictModel):
    id: str
    character_agent_id: str
    revision_number: int
    source_group_id: str | None = None
    change_type: Literal["axis", "subtitle", "aspect", "goal"]
    field_name: str
    previous_value: Any = None
    new_value: Any = None
    confidence: float | None = None
    justification: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_type: Literal["generated", "manual"]
    created_at: datetime

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def parse_change_evidence(cls, value: Any) -> list[str]:
        return _evidence_ids(value)

    @field_validator("previous_value", "new_value", mode="before")
    @classmethod
    def parse_change_value(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value


class CharacterGoalAssignmentCreate(_StrictModel):
    character_goal_id: str


class CharacterQueryResponseFormat(_StrictModel):
    type: Literal["text", "json"] = "text"
    schema_: dict[str, Any] | None = Field(None, alias="schema")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("schema_")
    @classmethod
    def require_bounded_schema(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(str(value)) > 20_000:
            raise ValueError("response schema is too large")
        def inspect(item: Any, depth: int = 0) -> None:
            if depth > 20:
                raise ValueError("response schema is too deeply nested")
            if isinstance(item, dict):
                reference = item.get("$ref")
                if isinstance(reference, str) and not reference.startswith("#"):
                    raise ValueError("remote schema references are not allowed")
                for child in item.values():
                    inspect(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    inspect(child, depth + 1)
        if value is not None:
            inspect(value)
            from jsonschema import Draft202012Validator
            from jsonschema.exceptions import SchemaError
            try:
                Draft202012Validator.check_schema(value)
            except SchemaError as exc:
                raise ValueError("invalid response JSON Schema") from exc
        return value


class CharacterQueryGeneration(_StrictModel):
    mode: Literal["simulation"] = "simulation"
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(500, ge=32, le=8_192)


class CharacterAgentQueryRequest(_StrictModel):
    query: str = Field(..., min_length=1, max_length=20_000)
    use_character_identity: bool = True
    system_instruction: str | None = Field(None, max_length=10_000)
    context: dict[str, Any] | None = None
    response_format: CharacterQueryResponseFormat = Field(default_factory=CharacterQueryResponseFormat)
    generation: CharacterQueryGeneration = Field(default_factory=CharacterQueryGeneration)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("context")
    @classmethod
    def bound_context(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(str(value)) > 50_000:
            raise ValueError("context is too large")
        return value


class CharacterAgentQueryResponse(_StrictModel):
    type: Literal["text", "json"]
    content: Any


# ── EmbodyAgent atomic service schemas ──────────────────────────────────

class SceneInput(_StrictModel):
    scene_id: str
    name: str
    description: str
    created_at: str | None = None


class EmotionalInterpretationOutput(_StrictModel):
    arousal: int = Field(..., ge=0, le=100)
    valence: int = Field(..., ge=-100, le=100)
    description: str = Field(..., min_length=1)


class CharacterBeliefOutput(_StrictModel):
    statement: str = Field(..., min_length=1)
    confidence: int = Field(..., ge=0, le=100)
    status: CharacterBeliefStatus


class CharacterImpactOutput(_StrictModel):
    impact_type: CharacterImpactType
    direction: CharacterImpactDirection
    magnitude: int = Field(..., ge=0, le=100)
    description: str = Field(..., min_length=1)


class ScenePerspectiveOutput(_StrictModel):
    scene_id: str
    source_type: ScenePerspectiveSourceType
    awareness_level: int = Field(..., ge=0, le=100)
    confidence: int = Field(..., ge=0, le=100)
    summary: str = Field(..., min_length=1)
    interpretation: str = Field(..., min_length=1)
    memory_strength: int = Field(..., ge=0, le=100)
    importance: int = Field(..., ge=1, le=5)
    status: ScenePerspectiveStatus = ScenePerspectiveStatus.ACTIVE
    emotional_interpretation: EmotionalInterpretationOutput | None = None
    belief: CharacterBeliefOutput | None = None
    impact: CharacterImpactOutput | None = None


class EmbodimentObservationsOutput(_StrictModel):
    recurring_behaviours: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    motivations: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    values: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    fears: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    conflicts: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    relationships: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    contradictions: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    evidence_gaps: list[EmbodimentEvidenceGap] = Field(default_factory=list)
    subtitle_change: SubtitleChangeProposal | None = None


class AxisChangeData(_StrictModel):
    axis: Literal[
        "calm_aggressive", "cautious_reckless", "compassionate_ruthless",
        "trusting_suspicious", "honest_deceptive", "patient_impulsive",
        "humble_proud", "cooperative_dominating",
    ]
    new_value: int = Field(..., ge=0, le=100)
    justification: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0, le=1)
    evidence_ids: list[str] = Field(..., min_length=1)


class AxisChangeOutput(_StrictModel):
    behavioural_axes: list[AxisChangeData] = Field(default_factory=list)


class AspectUpdateOperationType(str, Enum):
    ADD = "add"
    UPDATE = "update"
    REMOVE = "remove"


class AspectUpdateData(_StrictModel):
    operation: AspectUpdateOperationType
    name: str = Field(..., min_length=1, max_length=255)
    category: CharacterAspectCategory | None = None
    description: str | None = None
    importance: int | None = Field(None, ge=1, le=5)
    intensity: int | None = Field(None, ge=0, le=100)
    justification: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)


class AspectUpdateOutput(_StrictModel):
    aspect_updates: list[AspectUpdateData] = Field(default_factory=list)


class GoalUpdateOperationType(str, Enum):
    ADD = "add"
    UPDATE = "update"
    REMOVE = "remove"
    COMPLETE = "complete"


class GoalUpdateData(_StrictModel):
    operation: GoalUpdateOperationType
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    goal_type: CharacterGoalType | None = None
    priority: int | None = Field(None, ge=0, le=100)
    commitment: int | None = Field(None, ge=0, le=100)
    basis: Literal["explicit", "inferred"] | None = None
    justification: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)


class GoalUpdateOutput(_StrictModel):
    goal_updates: list[GoalUpdateData] = Field(default_factory=list)


class LLMCallRecord(_StrictModel):
    stage: str
    usage_tag: str
    input_chars: int
    output_chars: int
    input_tokens_est: int
    output_tokens_est: int
    total_tokens_est: int


class EmbodyAgentResult(_StrictModel):
    source_entity_id: str
    source_entity_alias: str
    perspectives: list[ScenePerspectiveOutput]
    observations: EmbodimentObservationsOutput
    axis_updates: list[AxisChangeData]
    aspect_updates: list[AspectUpdateData]
    goal_updates: list[GoalUpdateData]
    subtitle_change: SubtitleChangeProposal = Field(default_factory=SubtitleChangeProposal)
    llm_calls: list[LLMCallRecord]

    @property
    def total_llm_calls(self) -> int:
        return len(self.llm_calls)

    @property
    def total_tokens_est(self) -> int:
        return sum(call.total_tokens_est for call in self.llm_calls)
