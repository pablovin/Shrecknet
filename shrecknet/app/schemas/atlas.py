"""Bounded, card-ready Atlas discovery projections; canonical graph data stays upstream."""
from pydantic import BaseModel, Field

from app.schemas.ontology_instance import SceneGraphSourcePageReference, SceneLocalOrder


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
    results: list[AtlasSceneRow]
    total: int = Field(ge=0)
    skip: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)

