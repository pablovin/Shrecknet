"""Schemas for ontology-scoped CharacterAgent graph administration."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CharacterAgentStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


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


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CharacterAgentCreate(_StrictModel):
    ontology_id: int = Field(..., ge=1)
    entity_instance_id: str = Field(..., min_length=1)
    name: str | None = Field(None, min_length=1, max_length=255)
    background_story: str | None = Field(None, min_length=1)
    image_url: str | None = Field(None, max_length=2048)
    status: CharacterAgentStatus = CharacterAgentStatus.ACTIVE
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

    @field_validator("name", "background_story")
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
    background_story: str | None = Field(None, min_length=1)
    image_url: str | None = Field(None, max_length=2048)
    status: CharacterAgentStatus | None = None
    calm_aggressive: int | None = Field(None, ge=0, le=100)
    cautious_reckless: int | None = Field(None, ge=0, le=100)
    compassionate_ruthless: int | None = Field(None, ge=0, le=100)
    trusting_suspicious: int | None = Field(None, ge=0, le=100)
    honest_deceptive: int | None = Field(None, ge=0, le=100)
    patient_impulsive: int | None = Field(None, ge=0, le=100)
    humble_proud: int | None = Field(None, ge=0, le=100)
    cooperative_dominating: int | None = Field(None, ge=0, le=100)
    trait_adherence: int | None = Field(None, ge=0, le=100)

    @field_validator("name", "background_story")
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
    obtained_from_scene_id: str | None = None
    created_at: datetime
    updated_at: datetime


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
    created_at: datetime
    updated_at: datetime


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
    obtained_from_scene_id: str | None = None
    created_at: datetime
    updated_at: datetime


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
