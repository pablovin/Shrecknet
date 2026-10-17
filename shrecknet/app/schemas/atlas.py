"""Bounded, card-ready Atlas discovery projections; canonical graph data stays upstream."""
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ontology_instance import OntologyInstanceRead, SceneGraphSourcePageReference, SceneLocalOrder


class AtlasSourceCard(SceneGraphSourcePageReference):
    scene_count: int = Field(ge=0)
    latest_activity: str | None = None


class AtlasSourcePage(BaseModel):
    results: list[AtlasSourceCard]
    skip: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    has_more: bool


class AtlasSourceAnchor(BaseModel):
    entity_instance_id: str


class AtlasIndexScene(BaseModel):
    id: str
    name: str
    description: str | None = None
    derived_from: AtlasSourceAnchor | None = None
    local_order: SceneLocalOrder
    milestone_count: int = Field(ge=0)


class AtlasOwningInstance(BaseModel):
    instance_id: str
    name: str


class AtlasSceneRow(BaseModel):
    scene: AtlasIndexScene
    instance: AtlasOwningInstance
    source_page: SceneGraphSourcePageReference | None = None
    perspective_count: int = Field(ge=0)


class AtlasScenePage(BaseModel):
    related_only: bool = False
    results: list[AtlasSceneRow]
    total: int = Field(ge=0)
    skip: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)


class AtlasFolioProjectionRead(BaseModel):
    scenes: Literal["omitted"]
    relationships: Literal["omitted"]


class AtlasFolioDetailRead(OntologyInstanceRead):
    folio_projection: AtlasFolioProjectionRead


class AtlasAuthor(BaseModel):
    id: str
    type: Literal["human", "agent"]
    display_name: str
    avatar_url: str | None = None


class AtlasRelationshipTarget(BaseModel):
    entity_id: str
    page_id: str
    definition_id: int
    title: str
    avatar_url: str | None = None


class AtlasRelationshipRow(BaseModel):
    id: str
    definition_id: int
    direction: Literal["incoming", "outgoing"]
    focus_entity_id: str
    target: AtlasRelationshipTarget


class AtlasRelationshipGroup(BaseModel):
    key: str
    definition_id: int
    direction: Literal["incoming", "outgoing"]
    label: str | None = None
    total: int = Field(ge=0)


class AtlasRelationshipPage(BaseModel):
    results: list[AtlasRelationshipRow]
    groups: list[AtlasRelationshipGroup]
    total: int = Field(ge=0)
    skip: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)


class AtlasWebNode(BaseModel):
    id: str
    kind: Literal["entity", "source", "scene", "milestone"]
    name: str
    page_id: str


class AtlasWebEdge(BaseModel):
    source: str
    target: str
    label: str


class AtlasWorldWeb(BaseModel):
    ontology_id: int
    nodes: list[AtlasWebNode]
    edges: list[AtlasWebEdge]
    truncated: bool
