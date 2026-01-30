from __future__ import annotations

from pydantic import BaseModel, Field


class DefaultWorldsRequest(BaseModel):
    worlds: list[str] = Field(
        default_factory=list,
        description="World identifiers to create (fantasy, horror, scifi)",
    )


class DefaultWorldEntityResult(BaseModel):
    id: int
    name: str
    image_url: str | None = None


class DefaultWorldRelationshipResult(BaseModel):
    id: int
    name: str
    source_entity_id: int
    destiny_entity_id: int


class DefaultWorldResult(BaseModel):
    ontology_id: int
    name: str
    entities: list[DefaultWorldEntityResult] = Field(default_factory=list)
    relationships: list[DefaultWorldRelationshipResult] = Field(default_factory=list)


class DefaultWorldsResponse(BaseModel):
    created: list[DefaultWorldResult] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
