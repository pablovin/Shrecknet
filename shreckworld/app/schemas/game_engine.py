from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RulesetCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    edition: str = Field(min_length=1, max_length=100)
    schema_version: str = Field(default="1.0", min_length=1, max_length=32)

    @field_validator("name", "edition", "schema_version")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value must not be blank")
        return value.strip()


class RulesetRead(StrictModel):
    id: str
    name: str
    edition: str
    schema_version: str
    definition_revision: int
    status: str
    created_by: str
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class BindingCreate(StrictModel):
    ontology_id: int = Field(gt=0)
    ruleset_id: str = Field(min_length=1, max_length=36)


class BindingRead(StrictModel):
    id: str
    ontology_id: int
    ruleset_id: str
    status: str
    created_by: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class DefinitionCreate(StrictModel):
    kind: Literal["entity_schema", "content", "action", "resolution", "procedure", "progression"]
    schema_version: str = Field(min_length=1, max_length=32)
    payload: dict[str, object]
    provenance: list[SourceReference] = Field(default_factory=list, max_length=100)


class SourceReference(StrictModel):
    source_id: str = Field(min_length=1, max_length=36)
    document_id: str = Field(min_length=1, max_length=36)
    document_revision_id: str = Field(min_length=1, max_length=36)
    content_digest: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    locator_kind: Literal["physical_page", "printed_page", "passage", "section"]
    locator: str = Field(min_length=1, max_length=500)


class DefinitionRead(StrictModel):
    id: str
    kind: str
    schema_version: str
    payload: dict[str, object]
    provenance: list[dict[str, object]]
    status: str

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PublishRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    supersedes_version_id: str | None = Field(default=None, max_length=36)


class RejectRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Review reason must not be blank")
        return value.strip()


class ReviewRead(StrictModel):
    id: str
    ruleset_id: str
    definition_revision: int
    decision: str
    reviewer_id: str
    reason: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class VersionRead(StrictModel):
    id: str
    ruleset_id: str
    definition_revision: int
    schema_version: str
    definition_digest: str
    definitions_snapshot: list[dict[str, object]]
    dependency_pins: list[dict[str, object]]
    status: str
    published_by: str
    published_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class RunCreate(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    binding_id: str | None = Field(default=None, max_length=36)
    source_refs: list[SourceReference] = Field(default_factory=list, max_length=100)


class EntityDiscoveryCreate(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    binding_id: str = Field(min_length=1, max_length=36)
    source_ids: list[str] = Field(min_length=1, max_length=20)


class EntityDiscoveryReview(StrictModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)
    candidate_ids: list[str] = Field(default_factory=list, max_length=500)


class RunRead(StrictModel):
    id: str
    ruleset_id: str
    binding_id: str | None
    ontology_id: int | None
    owner_id: str
    status: str
    attempt: int
    retry_of_id: str | None
    input_sources: list[dict[str, object]]
    diagnostics: list[dict[str, object]]
    result: dict[str, object] | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    updated_at: datetime | None
    finished_at: datetime | None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class RunPage(StrictModel):
    items: list[RunRead]
    next_cursor: str | None


class TableCreate(StrictModel):
    ontology_id: int = Field(gt=0)
    version_id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name must not be blank")
        return value.strip()


class TableUpdate(StrictModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name must not be blank")
        return value.strip()


class ParticipantCreate(StrictModel):
    user_id: str = Field(min_length=1, max_length=64)
    role: Literal["owner", "player", "observer"] = "player"
    character_agent_id: str | None = Field(default=None, max_length=64)


class ParticipantRead(StrictModel):
    id: str
    user_id: str
    role: str
    status: str
    character_agent_id: str | None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class TableRead(StrictModel):
    id: str
    ontology_id: int
    version_id: str
    name: str
    status: str
    revision: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    participants: list[ParticipantRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class TablePage(StrictModel):
    items: list[TableRead]
    next_cursor: str | None
