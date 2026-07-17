"""Semantic-document contracts and deterministic hashing."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any


SOURCE_KINDS = {
    "ontology_entity_definition",
    "ontology_relationship_definition",
    "entity_profile",
    "entity_text_chunk",
    "scene",
    "milestone",
}


def stable_hash(value: Any) -> str:
    payload = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class SemanticDocument:
    document_id: str
    source_kind: str
    source_node_id: str | None
    ontology_id: int
    display_text: str
    embedding_text: str
    instance_id: str | None = None
    entity_definition_id: int | None = None
    relationship_definition_id: int | None = None
    scene_id: str | None = None
    source_field: str | None = None
    source_text_hash: str | None = None
    chunk_index: int = 0
    chunk_count: int = 1
    related_entity_ids: list[str] = field(default_factory=list)
    derived_from_entity_id: str | None = None
    source_created_at: str | None = None
    source_updated_at: str | None = None

    def __post_init__(self) -> None:
        if self.source_kind not in SOURCE_KINDS:
            raise ValueError(f"Unsupported semantic source kind: {self.source_kind}")
        self.display_text = self.display_text.strip()
        self.embedding_text = self.embedding_text.strip()
        if not self.display_text or not self.embedding_text:
            raise ValueError("Semantic documents require display and embedding text")
        self.related_entity_ids = sorted(set(self.related_entity_ids))

    @property
    def content_hash(self) -> str:
        return stable_hash(self.embedding_text)

    @property
    def metadata_hash(self) -> str:
        return stable_hash(
            {
                "source_kind": self.source_kind,
                "source_node_id": self.source_node_id,
                "ontology_id": self.ontology_id,
                "instance_id": self.instance_id,
                "entity_definition_id": self.entity_definition_id,
                "relationship_definition_id": self.relationship_definition_id,
                "scene_id": self.scene_id,
                "source_field": self.source_field,
                "source_text_hash": self.source_text_hash,
                "chunk_index": self.chunk_index,
                "chunk_count": self.chunk_count,
                "related_entity_ids": self.related_entity_ids,
                "derived_from_entity_id": self.derived_from_entity_id,
                "source_created_at": self.source_created_at,
                "source_updated_at": self.source_updated_at,
            }
        )
