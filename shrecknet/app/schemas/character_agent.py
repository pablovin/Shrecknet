"""Schemas for ontology-scoped CharacterAgent graph administration."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.models.character_embodiment import CharacterEmbodimentDraftStatus
from app.schemas.character_traits import (
    SlotKey, TraitKey, TraitEdit, TraitProfile, TraitObservation, TraitEvidence, TraitChange,
)


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


class IdentityPersonalityTrait(_StrictModel):
    trait: TraitKey
    description: str = Field(..., min_length=1)


class IdentityDescription(_StrictModel):
    identity_summary: str = Field(..., min_length=1)
    psychological_summary: str = Field(..., min_length=1)
    personality_traits: list[IdentityPersonalityTrait] = Field(...)


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
    trait_edits: dict[SlotKey, TraitEdit] = Field(default_factory=dict)

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
    trait_edits: dict[SlotKey, TraitEdit] = Field(default_factory=dict)

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
    trait_edits: dict[SlotKey, TraitEdit] = Field(default_factory=dict, exclude=True)
    trait_profile: TraitProfile = Field(default_factory=TraitProfile)
    identity_description: IdentityDescription | None = None
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
    trait_evidence: list[TraitObservation] = Field(default_factory=list)
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


class EmbodimentAspectProposal(_StrictModel):
    suggestion_id: str
    name: str = Field(..., min_length=1, max_length=255)
    category: CharacterAspectCategory
    description: str | None = None
    status: CharacterAspectStatus = CharacterAspectStatus.ACTIVE
    in_focus: bool = True
    justification: str = Field(default="", max_length=500)
    evidence_ids: list[str] = Field(default_factory=list)


class EmbodimentGoalProposal(_StrictModel):
    suggestion_id: str
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    goal_type: CharacterGoalType
    status: CharacterGoalStatus = CharacterGoalStatus.ACTIVE
    in_focus: bool = True
    justification: str = Field(default="", max_length=500)
    evidence_ids: list[str] = Field(default_factory=list)


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
    trait_profile: TraitProfile = Field(default_factory=TraitProfile)
    identity_description: IdentityDescription | None = None
    aspects: list[EmbodimentAspectProposal] = Field(default_factory=list)
    goals: list[EmbodimentGoalProposal] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_suggestion_ids(self):
        identifiers = [
            item.suggestion_id for item in [*self.aspects, *self.goals]
        ]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("aspect and goal suggestion IDs must be unique")
        if sum(item.status == CharacterAspectStatus.ACTIVE and item.in_focus for item in self.aspects) > 10:
            raise ValueError("at most ten aspects may be in focus")
        if sum(item.status == CharacterGoalStatus.ACTIVE and item.in_focus for item in self.goals) > 10:
            raise ValueError("at most ten goals may be in focus")
        return self


class CharacterAgentEmbeddedAspect(_StrictModel):
    suggestion_id: str | None = None
    name: str = Field(..., min_length=1, max_length=255)
    category: CharacterAspectCategory
    description: str | None = None
    status: CharacterAspectStatus = CharacterAspectStatus.ACTIVE
    in_focus: bool = True
    justification: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class CharacterAgentEmbeddedGoal(_StrictModel):
    suggestion_id: str | None = None
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    goal_type: CharacterGoalType
    status: CharacterGoalStatus = CharacterGoalStatus.ACTIVE
    in_focus: bool = True
    justification: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class CharacterAgentCreateRequest(CharacterAgentCreate):
    embodiment_draft_id: str | None = Field(None, min_length=1)
    aspects: list[CharacterAgentEmbeddedAspect] = Field(default_factory=list)
    goals: list[CharacterAgentEmbeddedGoal] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_focus_capacity(self):
        if sum(item.status == CharacterAspectStatus.ACTIVE and item.in_focus for item in self.aspects) > 10:
            raise ValueError("at most ten aspects may be in focus")
        if sum(item.status == CharacterGoalStatus.ACTIVE and item.in_focus for item in self.goals) > 10:
            raise ValueError("at most ten goals may be in focus")
        return self


class CharacterAgentEmbodimentUpdate(CharacterAgentUpdate):
    embodiment_draft_id: str | None = Field(None, min_length=1)
    aspects: list[CharacterAgentEmbeddedAspect] | None = None
    goals: list[CharacterAgentEmbeddedGoal] | None = None

    @model_validator(mode="after")
    def require_reviewed_assignments(self):
        if self.embodiment_draft_id and (self.aspects is None or self.goals is None):
            raise ValueError("reviewed embodiment updates must include aspects and goals")
        if self.aspects is not None and sum(item.status == CharacterAspectStatus.ACTIVE and item.in_focus for item in self.aspects) > 10:
            raise ValueError("at most ten aspects may be in focus")
        if self.goals is not None and sum(item.status == CharacterGoalStatus.ACTIVE and item.in_focus for item in self.goals) > 10:
            raise ValueError("at most ten goals may be in focus")
        return self


class EmbodimentDraftCreate(_StrictModel):
    ontology_id: int = Field(..., ge=1)
    entity_instance_id: str = Field(..., min_length=1)
    target_character_agent_id: str | None = Field(None, min_length=1)
    replace_existing: bool = False


class EmbodimentDraftStart(_StrictModel):
    draft_id: str
    job_id: int
    status: CharacterEmbodimentDraftStatus
    draft_url: str
    job_url: str


class EmbodimentDraftSummary(_StrictModel):
    id: str
    ontology_id: int
    source_entity_id: str
    target_character_agent_id: str | None = None
    status: CharacterEmbodimentDraftStatus
    background_job_id: int | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


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
    evidence_ids: list[str] = Field(default_factory=list)
    generated_by_embodiment_draft_id: str | None = None
    obtained_from_scene_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def project_legacy_definition(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = dict(value)
            for key in ("status", "importance", "intensity", "confidence", "justification"):
                value.pop(key, None)
        return value

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def parse_evidence_ids(cls, value: Any) -> list[str]:
        return _evidence_ids(value)


class CharacterAspectAssignmentCreate(_StrictModel):
    character_aspect_id: str
    status: CharacterAspectStatus = CharacterAspectStatus.ACTIVE
    in_focus: bool = True


class CharacterAspectAssignmentUpdate(_StrictModel):
    status: CharacterAspectStatus | None = None
    in_focus: bool | None = None


class CharacterAspectAssignmentRead(_StrictModel):
    aspect: CharacterAspectRead
    status: CharacterAspectStatus
    in_focus: bool
    justification: str | None = None
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
    evidence_ids: list[str] = Field(default_factory=list)
    generated_by_embodiment_draft_id: str | None = None
    obtained_from_scene_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def project_legacy_definition(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = dict(value)
            for key in ("status", "priority", "commitment", "confidence", "basis", "justification"):
                value.pop(key, None)
        return value

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def parse_evidence_ids(cls, value: Any) -> list[str]:
        return _evidence_ids(value)


class CharacterGoalAssignmentRead(_StrictModel):
    goal: CharacterGoalRead
    status: CharacterGoalStatus
    in_focus: bool
    justification: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def parse_evidence_ids(cls, value: Any) -> list[str]:
        return _evidence_ids(value)


class CharacterGoalAssignmentUpdate(_StrictModel):
    status: CharacterGoalStatus | None = None
    in_focus: bool | None = None


class _NarrativeFields(_StrictModel):
    @field_validator("description", "summary", "interpretation", "statement", "perspective", check_fields=False)
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


class CharacterBeliefUpdate(_NarrativeFields):
    statement: str | None = Field(None, min_length=1)
    confidence: int | None = Field(None, ge=0, le=100)


class CharacterBeliefRead(CharacterBeliefCreate):
    id: str
    ontology_id: int
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def project_legacy_status(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = dict(value)
            value.pop("status", None)
        return value


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
    perspective: str = Field(..., min_length=1)


class ScenePerspectiveUpdate(_NarrativeFields):
    source_type: ScenePerspectiveSourceType | None = None
    perspective: str | None = Field(None, min_length=1)


class ScenePerspectiveRead(_StrictModel):
    source_digest: str | None = None
    id: str
    ontology_id: int
    character_agent_id: str
    scene_id: str
    generated_with_revision_id: str | None = None
    source_group_id: str | None = None
    source_type: ScenePerspectiveSourceType
    perspective: str
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


class DisplayReference(_StrictModel):
    """Human-readable identity for a UI-facing timeline reference."""
    id: str
    type: Literal["scene", "source_group", "aspect", "goal", "evidence"]
    name: str
    description: str | None = None
    instance_name: str | None = None


class ProjectedCharacterImpact(_StrictModel):
    impact_type: CharacterImpactType
    target_id: str = Field(..., min_length=1)
    direction: CharacterImpactDirection
    magnitude: int = Field(..., ge=0, le=100)
    description: str = Field(..., min_length=1)
    target: DisplayReference | None = None


class ProjectedTraitChange(TraitChange):
    evidence: list[DisplayReference] = Field(default_factory=list)


class ProjectedScenePerspective(_StrictModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_digest: str | None = None
    scene_id: str
    scene: DisplayReference | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    evidence: list[DisplayReference] = Field(default_factory=list)
    source_type: ScenePerspectiveSourceType
    perspective: str = Field(..., min_length=1)
    emotions: list["EmotionalInterpretationOutput"] = Field(default_factory=list)
    beliefs: list["CharacterBeliefOutput"] = Field(default_factory=list)
    impacts: list[ProjectedCharacterImpact] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def discard_retired_behavioral_evidence(cls, value: Any) -> Any:
        """Read older persisted draft projections without re-exposing the retired field."""
        if isinstance(value, dict) and "behavioral_evidence" in value:
            value = dict(value)
            value.pop("behavioral_evidence", None)
        return value


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
    trait_profile: TraitProfile = Field(default_factory=TraitProfile)
    trait_evidence: list[TraitEvidence] = Field(default_factory=list)
    batch_id: str | None = None
    scene_ids: list[str] = Field(default_factory=list)
    active_aspects: list[EmbodimentAspectProposal] = Field(default_factory=list)
    active_goals: list[EmbodimentGoalProposal] = Field(default_factory=list)


class CharacterSourceProjection(_StrictModel):
    source_group_id: str
    source_group: DisplayReference | None = None
    starting_revision_number: int = Field(..., ge=0)
    perspectives: list[ProjectedScenePerspective]
    trait_changes: list[ProjectedTraitChange] = Field(default_factory=list)
    batch_id: str | None = None
    aspects: list[EmbodimentAspectProposal] = Field(default_factory=list)
    goals: list[EmbodimentGoalProposal] = Field(default_factory=list)
    completed_goal_titles: list[str] = Field(default_factory=list)
    aspect_operations: list[AspectUpdateData] = Field(default_factory=list)
    goal_operations: list[GoalUpdateData] = Field(default_factory=list)
    focused_aspects: list[str] = Field(default_factory=list, max_length=10)
    focused_goals: list[str] = Field(default_factory=list, max_length=10)
    subtitle_change: SubtitleChangeProposal = Field(default_factory=SubtitleChangeProposal)
    llm_calls: list["LLMCallRecord"] = Field(default_factory=list)
    resulting_revision: CharacterIdentityRevisionProjection


class CharacterTimelineProjection(_StrictModel):
    revisions: list[CharacterIdentityRevisionProjection] = Field(..., min_length=1)
    source_projections: list[CharacterSourceProjection] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_chronology(self):
        if len(self.revisions) != len(self.source_projections) + 1:
            raise ValueError("timeline requires one baseline and one revision per batch")
        seen_scenes: set[str] = set()
        for previous, revision, projection in zip(
            self.revisions, self.revisions[1:], self.source_projections
        ):
            if (revision.revision_number != previous.revision_number + 1
                    or projection.starting_revision_number != previous.revision_number
                    or projection.resulting_revision != revision):
                raise ValueError("timeline revisions must match consecutive batch projections")
            scene_ids = [item.scene_id for item in projection.perspectives]
            if (scene_ids != revision.scene_ids or len(set(scene_ids)) != len(scene_ids)
                    or seen_scenes.intersection(scene_ids)):
                raise ValueError("timeline scenes must occur exactly once in their batch order")
            if (projection.batch_id != revision.batch_id
                    or projection.source_group_id != revision.source_group_id):
                raise ValueError("timeline batch provenance must match its revision")
            perspective_scenes = {item.id: item.scene_id for item in projection.perspectives}
            if len(perspective_scenes) != len(projection.perspectives):
                raise ValueError("timeline perspective IDs must be unique")
            observed_traits: set[tuple[str, str]] = set()
            for item in revision.trait_evidence:
                if (item.evidence_kind != "behavior"
                        or item.perspective_id not in perspective_scenes
                        or item.evidence_ids != [f"scene:{perspective_scenes[item.perspective_id]}"]
                        or item.source_group_id != projection.source_group_id):
                    raise ValueError("trait evidence must cite a perspective and scene in its source batch")
                key = (item.perspective_id, item.trait)
                if key in observed_traits:
                    raise ValueError("one trait observation is allowed per perspective")
                observed_traits.add(key)
            seen_scenes.update(scene_ids)
        return self


class CharacterIdentityRevisionRead(_StrictModel):
    id: str
    character_agent_id: str
    revision_number: int
    source_group_id: str | None = None
    last_processed_scene_id: str | None = None
    name: str
    subtitle: str | None = None
    trait_profile: TraitProfile
    batch_id: str | None = None
    scene_ids: list[str] = Field(default_factory=list)
    active_aspect_ids: list[str] = Field(default_factory=list)
    active_goal_ids: list[str] = Field(default_factory=list)
    provenance_type: Literal["generated", "manual", "initial"]
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    created_at: datetime

    @field_validator("trait_profile", "scene_ids", "active_aspect_ids", "active_goal_ids", mode="before")
    @classmethod
    def parse_revision_json(cls, value: Any):
        if isinstance(value, str):
            return json.loads(value)
        return value


class CharacterIdentityChangeRead(_StrictModel):
    id: str
    character_agent_id: str
    revision_number: int
    source_group_id: str | None = None
    change_type: Literal["trait", "steadiness", "subtitle", "aspect", "goal"]
    observation_ids: list[str] = Field(default_factory=list)
    actor_user_id: int | None = None
    policy_version: str | None = None
    field_name: str
    previous_value: Any = None
    new_value: Any = None
    confidence: float | None = None
    justification: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_type: Literal["generated", "manual"]
    created_at: datetime

    @field_validator("evidence_ids", "observation_ids", mode="before")
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
    temperature: float = Field(0.7, ge=0.0, le=2.0)


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


class CharacterAgentQueryResult(_StrictModel):
    type: Literal["text", "json"]
    content: Any
    decision_basis: str


class CharacterAgentQueryQueued(_StrictModel):
    job_id: int
    status: Literal["queued"] = "queued"
    stage: Literal["queued"] = "queued"
    progress: float = 0.0
    status_url: str


class CharacterAgentQueryError(_StrictModel):
    code: str
    message: str


class CharacterAgentQueryJobRead(_StrictModel):
    job_id: int
    character_agent_id: str
    status: Literal["queued", "running", "done", "failed"]
    stage: Literal[
        "queued", "loading_identity", "retrieving_memories", "deliberating",
        "repairing", "validating", "completed", "failed",
    ]
    progress: float = Field(ge=0.0, le=1.0)
    result: CharacterAgentQueryResult | None = None
    error: CharacterAgentQueryError | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


# ── EmbodyAgent atomic service schemas ──────────────────────────────────

class SceneInput(_StrictModel):
    scene_id: str
    name: str
    description: str
    created_at: str | None = None


class EmotionalInterpretationOutput(_StrictModel):
    arousal: int = Field(..., ge=0, le=100)
    valence: int = Field(..., ge=0, le=100)
    description: str = Field(..., min_length=1)

    @field_validator("valence", mode="before")
    @classmethod
    def normalize_legacy_signed_valence(cls, value: Any) -> Any:
        """Read old generated signed valence while writing the public 0..100 scale."""
        if isinstance(value, int) and value < 0:
            return round((value + 100) / 2)
        return value


class CharacterBeliefOutput(_StrictModel):
    statement: str = Field(..., min_length=1)
    confidence: int = Field(..., ge=0, le=100)


class ProfileEventOutput(_StrictModel):
    kind: Literal["aspect", "goal"]
    description: str = Field(..., min_length=1, max_length=300)
    scene_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ScenePerspectiveOutput(_StrictModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    scene_id: str
    evidence_ids: list[str] = Field(min_length=1)
    source_type: ScenePerspectiveSourceType
    perspective: str = Field(..., min_length=1)


class SceneEnrichmentOutput(_StrictModel):
    scene_id: str
    evidence_ids: list[str] = Field(min_length=1)
    # These keys are required in generated enrichment output. An empty list is a
    # valid, explicit no-op; an omitted key is a contract failure that must be
    # corrected rather than silently defaulting to an empty list.
    emotions: list[EmotionalInterpretationOutput] = Field(...)
    beliefs: list[CharacterBeliefOutput] = Field(...)
    profile_events: list[ProfileEventOutput] = Field(default_factory=list)
    trait_candidates: list[TraitObservation] = Field(...)

class SceneEnrichmentsOutput(_StrictModel):
    scene_enrichments: list[SceneEnrichmentOutput]


class ScenePerspectiveBundleOutput(ScenePerspectiveOutput):
    emotions: list[EmotionalInterpretationOutput] = Field(default_factory=list)
    beliefs: list[CharacterBeliefOutput] = Field(default_factory=list)
    profile_events: list[ProfileEventOutput] = Field(default_factory=list)
    trait_candidates: list[TraitObservation] = Field(default_factory=list)


class EmbodimentObservationsOutput(_StrictModel):
    trait_evidence: list[TraitObservation] = Field(default_factory=list)
    recurring_behaviours: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    motivations: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    values: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    fears: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    conflicts: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    relationships: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    contradictions: list[EmbodimentGroundedStatement] = Field(default_factory=list)
    evidence_gaps: list[EmbodimentEvidenceGap] = Field(default_factory=list)
    subtitle_change: SubtitleChangeProposal | None = None


class AspectUpdateOperationType(str, Enum):
    ADD = "add"
    UPDATE = "update"
    STATUS = "status"
    REINFORCE = "reinforce"


class AspectUpdateData(_StrictModel):
    operation: AspectUpdateOperationType
    target_id: str | None = None
    candidate_id: str | None = None
    name: str = Field(..., min_length=1, max_length=255)
    category: CharacterAspectCategory | None = None
    description: str | None = None
    status: CharacterAspectStatus | None = None
    in_focus: bool | None = None
    justification: str = Field(..., min_length=1)
    evidence_ids: list[str] = Field(..., min_length=1)
    event_references: list[int] = Field(default_factory=list)


class GoalUpdateOperationType(str, Enum):
    ADD = "add"
    UPDATE = "update"
    STATUS = "status"
    REINFORCE = "reinforce"


class GoalUpdateData(_StrictModel):
    operation: GoalUpdateOperationType
    target_id: str | None = None
    candidate_id: str | None = None
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    goal_type: CharacterGoalType | None = None
    status: CharacterGoalStatus | None = None
    in_focus: bool | None = None
    justification: str = Field(..., min_length=1)
    evidence_ids: list[str] = Field(..., min_length=1)
    event_references: list[int] = Field(default_factory=list)


class LLMCallRecord(_StrictModel):
    stage: str
    usage_tag: str
    provider: str
    model: str
    input_chars: int
    output_chars: int
    input_tokens_est: int
    output_tokens_est: int
    total_tokens_est: int


class EmbodyAgentAnalysis(_StrictModel):
    scene_input_digests: dict[str, str] = Field(default_factory=dict)
    """Snapshot-based source interpretation before ordered profile mutation."""

    source_entity_id: str
    source_entity_alias: str
    identity_description: IdentityDescription | None = None
    perspectives: list[ScenePerspectiveBundleOutput]
    observations: EmbodimentObservationsOutput
    subtitle_change: SubtitleChangeProposal = Field(default_factory=SubtitleChangeProposal)
    evidence_ids: set[str] = Field(default_factory=set)
    profile_events: list[ProfileEventOutput] = Field(default_factory=list)
    llm_calls: list[LLMCallRecord]
    observations_unavailable: bool = False


class EmbodyAgentResult(_StrictModel):
    scene_input_digests: dict[str, str] = Field(default_factory=dict)
    source_entity_id: str
    source_entity_alias: str
    perspectives: list[ScenePerspectiveBundleOutput]
    observations: EmbodimentObservationsOutput
    trait_profile: TraitProfile
    trait_evidence: list[TraitEvidence] = Field(default_factory=list)
    trait_changes: list[TraitChange] = Field(default_factory=list)
    batch_id: str | None = None
    aspect_updates: list[AspectUpdateData]
    goal_updates: list[GoalUpdateData]
    focused_aspects: list[str] = Field(default_factory=list, max_length=10)
    focused_goals: list[str] = Field(default_factory=list, max_length=10)
    subtitle_change: SubtitleChangeProposal = Field(default_factory=SubtitleChangeProposal)
    llm_calls: list[LLMCallRecord]

    @property
    def total_llm_calls(self) -> int:
        return len(self.llm_calls)

    @property
    def total_tokens_est(self) -> int:
        return sum(call.total_tokens_est for call in self.llm_calls)
