"""Public, bounded Atlas discovery response models."""
from pydantic import BaseModel, Field


class AtlasSourceReference(BaseModel):
    id: str
    display_name: str
    avatar_url: str | None = None


class AtlasSourceCard(AtlasSourceReference):
    scene_count: int
    latest_activity: str | None = None


class AtlasSourcePage(BaseModel):
    results: list[AtlasSourceCard]
    skip: int
    limit: int
    has_more: bool


class AtlasSourceAnchor(BaseModel):
    entity_instance_id: str


class AtlasSceneOrder(BaseModel):
    followed_by_scene_id: str | None = None
    preceded_by_scene_id: str | None = None


class AtlasScene(BaseModel):
    id: str
    name: str
    description: str | None = None
    derived_from: AtlasSourceAnchor | None = None
    local_order: AtlasSceneOrder = Field(default_factory=AtlasSceneOrder)
    milestone_count: int


class AtlasOwningInstance(BaseModel):
    instance_id: str
    name: str


class AtlasSceneRow(BaseModel):
    scene: AtlasScene
    instance: AtlasOwningInstance
    source_page: AtlasSourceReference | None = None
    perspective_count: int


class AtlasScenePage(BaseModel):
    results: list[AtlasSceneRow]
    skip: int
    limit: int
    total: int


class AtlasElderCard(BaseModel):
    id: str
    name: str
    avatar_url: str | None = None
    description: str | None = None
