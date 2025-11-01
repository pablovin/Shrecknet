from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExtractedNewInstance(BaseModel):
    alias: str = Field(..., min_length=1, description="Proposed instance alias")
    entity_definition_id: int = Field(..., description="Target ontology entity definition id")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    justification: str | None = None
    metadata: dict[str, Any] | None = None


class ExtractedExistingInstance(BaseModel):
    entity_instance_id: str = Field(..., description="Existing entity instance identifier")
    entity_definition_id: int = Field(..., description="Ontology entity definition id")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    justification: str | None = None
    metadata: dict[str, Any] | None = None


class ArchitectLLMResponse(BaseModel):
    new_instances: list[ExtractedNewInstance] = Field(default_factory=list)
    existing_instances: list[ExtractedExistingInstance] = Field(default_factory=list)


class ChunkAnalysisResult(BaseModel):
    """Internal container for chunk-level suggestions."""

    chunk_index: int
    chunk_text: str
    source_entity_alias: str | None = None
    source_entity_definition_id: int | None = None
    new_instances: list[ExtractedNewInstance] = Field(default_factory=list)
    existing_instances: list[ExtractedExistingInstance] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)
