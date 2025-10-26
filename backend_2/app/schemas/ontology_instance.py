from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, validator


class OntologyInstancePropertyValue(BaseModel):
    definition_id: int
    value: Any


class OntologyInstanceRelationshipCreate(BaseModel):
    definition_id: int
    target_alias: str
    data: dict[str, Any] = Field(default_factory=dict)


class OntologyInstanceRelationshipRead(BaseModel):
    relationship_instance_id: str
    definition_id: int
    target_entity_id: str
    destiny_entity_definition_id: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class OntologyInstanceEntityCreate(BaseModel):
    definition_id: int
    alias: str
    properties: list[OntologyInstancePropertyValue] = Field(default_factory=list)
    relationships: list[OntologyInstanceRelationshipCreate] = Field(default_factory=list)

    @validator("alias")
    def validate_alias(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("alias cannot be empty")
        return value


class OntologyInstanceEntityRead(BaseModel):
    entity_instance_id: str
    definition_id: int
    alias: str | None = None
    properties: list[OntologyInstancePropertyValue] = Field(default_factory=list)
    relationships: list[OntologyInstanceRelationshipRead] = Field(default_factory=list)


class OntologyInstanceBase(BaseModel):
    name: str
    description: str | None = None


class OntologyInstanceCreate(OntologyInstanceBase):
    ontology_id: int
    entities: list[OntologyInstanceEntityCreate]


class OntologyInstanceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    entities: list[OntologyInstanceEntityCreate] | None = None


class OntologyInstanceRead(OntologyInstanceBase):
    instance_id: str
    ontology_id: int
    created_at: datetime
    updated_at: datetime
    entities: list[OntologyInstanceEntityRead]
