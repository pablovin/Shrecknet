from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class User(BaseModel):
    id: int
    username: str
    full_name: str
    email: str
    timezone: str
    role: str
    avatar_url: str | None = None
    entity_ids: list[int] = Field(default_factory=list)


class World(BaseModel):
    id: str
    name: str
    ontology_ids: list[int] = Field(default_factory=list)


class Ontology(BaseModel):
    id: int
    name: str
    description: str | None = None
    image_url: str | None = None
    created_at: datetime
    updated_at: datetime


class OntologyWorldStatsItem(BaseModel):
    ontology_id: int
    entity_type_count: int = 0
    entity_instance_count: int = 0
    library_item_count: int = 0
    scene_count: int = 0
    milestone_count: int = 0
    updated_at: datetime


class OntologyWorldStatsResponse(BaseModel):
    results: list[OntologyWorldStatsItem] = Field(default_factory=list)


class OntologyInstanceCount(BaseModel):
    total: int


class OntologyInstanceEntity(BaseModel):
    entity_instance_id: str | None = None
    definition_id: int
    alias: str
    text: str | None = None
    author_type: str
    author_id: str
    properties: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)


class OntologyInstanceScene(BaseModel):
    id: str | None = None
    name: str
    description: str
    created_by_type: str
    created_by_author: str
    derived_from: dict[str, Any]
    milestones: list[dict[str, Any]] = Field(default_factory=list)
    relates_to: list[dict[str, Any]] = Field(default_factory=list)
    local_order: dict[str, Any] = Field(default_factory=dict)


class OntologyInstance(BaseModel):
    id: str
    ontology_id: int
    name: str
    entities: list[dict[str, Any]] = Field(default_factory=list)
    scenes: list[dict[str, Any]] = Field(default_factory=list)


class OntologyInstanceCreate(BaseModel):
    ontology_id: int
    name: str
    entities: list[OntologyInstanceEntity] = Field(default_factory=list)
    scenes: list[OntologyInstanceScene] = Field(default_factory=list)


class OntologyInstanceUpdate(BaseModel):
    name: str | None = None
    entities: list[OntologyInstanceEntity] | None = None
    scenes: list[OntologyInstanceScene] | None = None


class OntologyInstanceSummaryPage(BaseModel):
    total: int
    items: list[dict[str, Any]] = Field(default_factory=list)


class OntologyInstanceSearchResponse(BaseModel):
    entities: list[dict[str, Any]] = Field(default_factory=list)
    scenes: list[dict[str, Any]] = Field(default_factory=list)
    milestones: list[dict[str, Any]] = Field(default_factory=list)


class OntologyEntityResolveResponse(BaseModel):
    ontology_id: int
    results: list[dict[str, Any]] = Field(default_factory=list)
    missing_entity_instance_ids: list[str] = Field(default_factory=list)


class OntologyInstanceSceneCountsResponse(BaseModel):
    counts: dict[str, int] = Field(default_factory=dict)


class UserBootstrapStatus(BaseModel):
    has_users: bool


class AgentBase(BaseModel):
    name: str
    avatar_url: str | None = None
    description: str | None = None
    writing_style: str | None = None
    job: str = "elder"
    active: bool = True


class AgentCreate(AgentBase):
    ontology_ids: list[int] = Field(default_factory=list)


class AgentUpdate(BaseModel):
    name: str | None = None
    avatar_url: str | None = None
    description: str | None = None
    writing_style: str | None = None
    job: str | None = None
    active: bool | None = None


class AgentRead(AgentBase):
    id: str
    created_at: datetime
    updated_at: datetime
    ontology_ids: list[int] = Field(default_factory=list)


class ProviderValidation(BaseModel):
    configured: bool
    present: bool
    valid: bool | None = None
    error: str | None = None


class ProviderStatus(BaseModel):
    provider_id: str
    enabled: bool
    valid: bool | None = None
    configured: bool | None = None
    error: str | None = None
    models: list[str] = Field(default_factory=list)


class LLMReadinessReport(BaseModel):
    checks: dict[str, bool] = Field(default_factory=dict)
    providers: list[ProviderStatus] = Field(default_factory=list)
    ready: bool
    reasons: list[str] = Field(default_factory=list)
