"""Validated API contracts for world-local hierarchy collections."""
from __future__ import annotations

from typing import Annotated
from pydantic import Field, model_validator

from app.schemas.sources import Contract, SourceType, Identifier, Title

SafeEntryId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")]


class CollectionCreate(Contract):
    source_type: SourceType
    hierarchy_template_id: Identifier
    hierarchy_template_version: int = Field(ge=1)
    name: Title | None = None


class CollectionEntry(Contract):
    id: SafeEntryId
    parent_id: SafeEntryId | None = None
    level: int = Field(ge=0, le=31)
    order: int = Field(ge=0)
    title: Title
    description: str = Field(default="", max_length=10000)
    image_key: Annotated[str, Field(pattern=r"^[a-f0-9]{32}\.(?:png|jpg|webp)$")] | None = None
    source_ids: list[Identifier] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def unique_sources(self):
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("An entry cannot link the same source twice")
        return self


class CollectionUpdate(Contract):
    expected_revision: int = Field(ge=1)
    name: Title
    description: str = Field(default="", max_length=10000)
    archived: bool = False
    nodes: list[CollectionEntry] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def valid_tree(self):
        by_id = {node.id: node for node in self.nodes}
        if len(by_id) != len(self.nodes):
            raise ValueError("Duplicate entry IDs")
        order_keys: set[tuple[str | None, int]] = set()
        for node in self.nodes:
            if (node.parent_id, node.order) in order_keys:
                raise ValueError("Sibling order values must be unique")
            order_keys.add((node.parent_id, node.order))
            visited = {node.id}
            parent = node.parent_id
            while parent is not None:
                if parent in visited or parent not in by_id:
                    raise ValueError("Invalid or cyclic parent hierarchy")
                visited.add(parent)
                parent = by_id[parent].parent_id
        return self


class CollectionTemplateUpdate(Contract):
    expected_revision: int = Field(ge=1)
    template_version: int = Field(ge=1)


class CollectionRead(Contract):
    id: str
    ontology_id: int
    source_type: SourceType
    hierarchy_template_id: str
    hierarchy_template_version: int
    section_types: list[str]
    template_has_image: bool
    name: str
    description: str
    archived: bool
    revision: int
    nodes: list[CollectionEntry]
