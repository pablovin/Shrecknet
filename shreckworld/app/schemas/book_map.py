"""Typed Book Map V4 representation; separate from the legacy V3 API models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


StructuralNodeType = Literal["document", "part", "chapter", "section", "appendix"]
ContentNodeType = Literal[
    "paragraph", "list", "list_item", "table", "image", "caption",
    "equation", "unknown", "unparsed_region",
]
BookMapNodeType = Literal[
    "document", "part", "chapter", "section", "appendix", "paragraph",
    "list", "list_item", "table", "image", "caption", "equation",
    "unknown", "unparsed_region",
]
ResolutionStatus = Literal["detected", "resolved", "validated", "uncertain", "unresolved"]
StructuralSource = Literal["bookmark", "toc", "heading", "inferred"]
IndexAlignment = Literal["aligned", "legacy_context"]


class BookMapPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    physical_page: int = Field(ge=1)
    reading_order: int = Field(ge=0)
    x: float | None = None
    y: float | None = None


class BookMapPageLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    physical_page: int = Field(ge=1)
    printed_label: str | None = None
    reading_order: int | None = Field(default=None, ge=0)
    x: float | None = None
    y: float | None = None


class BookMapPageRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=1)
    end: int = Field(ge=1)


class BookMapEvidenceDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str
    rejected: bool = False


class BookMapEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    source_kind: Literal["bookmark", "toc_entry", "heading", "block", "page"]
    original_reference: str = Field(min_length=1)
    locator: BookMapPosition
    parser_name: str | None = None
    parser_version: str | None = None
    observed_features: list[str] = Field(default_factory=list)
    diagnostics: list[BookMapEvidenceDiagnostic] = Field(default_factory=list)


class BookMapNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    node_type: BookMapNodeType
    title: str | None = None
    position: BookMapPosition
    level: int = Field(ge=0)
    parent_id: str | None = None
    child_ids: list[str] = Field(default_factory=list)
    heading_path: list[str] = Field(default_factory=list)
    structural_node_id: str | None = None
    page_locators: list[BookMapPageLocator] = Field(default_factory=list)
    pages: BookMapPageRange | None = None
    source_block_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)
    caption_node_ids: list[str] = Field(default_factory=list)
    content_ref: str | None = None
    table_ref: str | None = None
    media_ref: str | None = None
    resolution_status: ResolutionStatus
    content_confidence: float | None = Field(default=None, ge=0, le=1)
    parent_confidence: float | None = Field(default=None, ge=0, le=1)
    structural_source: StructuralSource | None = None
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)


class BookMapQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_count: int = Field(ge=1)
    source_block_count: int = Field(ge=0)
    owned_block_count: int = Field(ge=0)
    unresolved_node_ids: list[str] = Field(default_factory=list)
    uncertain_node_ids: list[str] = Field(default_factory=list)
    validation_error_count: int = Field(default=0, ge=0)
    validation_warning_count: int = Field(default=0, ge=0)


class BookMapV4(BaseModel):
    model_config = ConfigDict(extra="forbid")

    map_version: Literal[4] = 4
    document_id: str = Field(min_length=1)
    source_sha256: str
    root_id: str = Field(min_length=1)
    resolver_version: str = Field(min_length=1)
    chunking_version: str = Field(min_length=1)
    nodes: list[BookMapNode]
    evidence: list[BookMapEvidence] = Field(default_factory=list)
    quality: BookMapQuality
    index_alignment: IndexAlignment

    @field_validator("source_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
            raise ValueError("source_sha256 must be a 64-character hexadecimal digest")
        return value.lower()
