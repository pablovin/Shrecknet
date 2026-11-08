"""Explicit owner-service and source-migration contracts."""
from typing import Literal
from pydantic import BaseModel, Field, StrictInt


class OntologyIdentity(BaseModel):
    id: StrictInt = Field(gt=0)
    name: str
    description: str | None = None


class WorldOntologyLink(BaseModel):
    id: str
    ontology_ids: list[StrictInt]


class OntologyMigrationRequest(BaseModel):
    mappings: dict[str, StrictInt] = Field(default_factory=dict)
    apply: bool = False


class OntologyMigrationEntry(BaseModel):
    collection_id: str
    name: str
    ontology_id: int | None
    item_count: int
    pending_items: int
    status: Literal['ready', 'needs_mapping']


class OntologyMigrationReport(BaseModel):
    collections: list[OntologyMigrationEntry]
    changed_items: int
