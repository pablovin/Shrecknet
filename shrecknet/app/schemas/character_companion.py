"""Typed boundaries for Character Companion HTTP, evidence and streaming."""
from __future__ import annotations
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

Tool = Literal["atlas", "shreckworld", "chronicle", "web", "character_memory"]
DIMENSIONS = {"connection": 25, "coordination": 20, "trust": 20, "familiarity": 0, "openness": 10, "current_strain": 0}
TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SelectionWrite(StrictModel):
    character_agent_id: str = Field(min_length=1, max_length=255)
    expected_version: int = Field(ge=0)


class VersionWrite(StrictModel):
    expected_version: int = Field(ge=0)


class PageContext(StrictModel):
    surface: Literal["global", "atlas", "shreckworld", "chronicle"] = "global"
    world_id: int | None = Field(None, ge=1)
    ontology_id: int | None = Field(None, ge=1)
    entity_instance_id: str | None = None
    source_id: str | None = None
    scene_id: str | None = None
    shreckworld_id: str | None = Field(None, max_length=64)
    library_item_ids: list[str] = Field(default_factory=list, max_length=100)
    chronicle_id: int | None = Field(None, ge=1)
    chronicle_revision: int | None = Field(None, ge=1)
    section_id: str | None = None
    selection_text: str | None = Field(None, max_length=4000)

    @model_validator(mode="after")
    def validate_scope(self):
        if len(set(self.library_item_ids)) != len(self.library_item_ids):
            raise ValueError("Duplicate library IDs")
        if self.library_item_ids and not self.shreckworld_id:
            raise ValueError("Books require a ShreckWorld")
        if self.surface == "shreckworld" and not self.shreckworld_id:
            raise ValueError("ShreckWorld surface requires its world ID")
        if self.surface == "chronicle" and not self.chronicle_id:
            raise ValueError("Chronicle surface requires a Chronicle ID")
        return self


class Tools(StrictModel):
    allowed: list[Tool] | None = None
    web: Literal["disabled", "allow", "require"] = "disabled"


class ChatCreate(StrictModel):
    character_agent_id: str = Field(min_length=1, max_length=255)
    selection_version: int = Field(ge=0)
    title: str | None = Field(None, min_length=1, max_length=255)
    default_scope: PageContext | None = None


class ChatUpdate(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    expected_revision: int = Field(ge=1)


class TurnCreate(StrictModel):
    text: str = Field("", max_length=8000)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=4)
    selection_version: int = Field(ge=0)
    context: PageContext | None = None
    tools: Tools = Field(default_factory=Tools)
    retry_of_turn_id: UUID | None = None

    @model_validator(mode="after")
    def validate_input(self):
        if not self.text.strip() and not self.attachment_ids:
            raise ValueError("Text or images required")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("Duplicate attachment IDs")
        if self.tools.web == "require" and self.tools.allowed is not None and "web" not in self.tools.allowed:
            raise ValueError("Required web tool excluded")
        return self


class Evidence(StrictModel):
    evidence_id: str
    source_id: str
    source_type: Literal["atlas", "shreckworld", "chronicle", "web", "character_memory", "user_attachment"]
    title: str
    excerpt: str
    structured_evidence: dict[str, Any] | None = None
    reference: dict[str, Any]
    relevance: dict[str, Any]
    coverage: dict[str, Any]
    epistemic_category: Literal["canonical_world", "document_evidence", "chronicle_record", "external_web", "subjective_memory", "user_provided"]
    retrieved_at: str
    scope: dict[str, Any]
    navigation: dict[str, Any]


# Response models are additive; keep transport contracts visible in OpenAPI.
from typing import Generic, TypeVar
from datetime import datetime
T = TypeVar('T')


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None


class CharacterSummary(BaseModel):
    id: str
    name: str
    subtitle: str | None = None
    image_url: str | None = None
    ontology_id: int


class CandidateRead(CharacterSummary):
    status: str
    visibility: str
    identity_ready: bool
    selectable: bool


class SelectionRead(BaseModel):
    character_agent_id: str | None
    version: int
    availability: Literal['none','ready','identity_pending','archived','unavailable']
    character: CharacterSummary | None
    selected_at: datetime | None
    updated_at: datetime | None


class RapportRead(BaseModel):
    character_agent_id: str
    scores: dict[str,int]
    version: int
    generation: int
    last_turn_id: str | None
    updated_at: datetime
    pending_count: int
    failed_count: int


class ChatRead(BaseModel):
    id: str
    user_id: int
    character_agent_id: str
    title: str
    default_scope: dict | None
    revision: int
    active_turn_id: str | None
    created_at: datetime
    updated_at: datetime


class MessageRead(BaseModel):
    id: str
    turn_id: str
    chat_id: str
    role: str
    content: str
    manifest: list[dict]
    committed: bool
    created_at: datetime


class TurnQueued(BaseModel):
    turn_id: str
    chat_id: str
    status: Literal['queued']
    user_message_id: str
    events_url: str
    result_url: str


class TurnRead(BaseModel):
    id: str
    turn_id: str
    chat_id: str
    status: Literal['queued','routing','retrieving','synthesizing','validating','completed','failed','cancelled','interrupted']
    draft: str
    metrics: dict = Field(default_factory=dict)
    result: dict | None
    error: dict | None
    rapport_status: str
    last_event_id: str
    created_at: datetime
    updated_at: datetime


class AttachmentRead(BaseModel):
    id: str
    user_id: int
    turn_id: str | None
    size: int
    width: int
    height: int
    sha256: str
    mime_type: str
    content_url: str
    created_at: datetime


class RapportAuditRead(BaseModel):
    id: str
    character_agent_id: str
    turn_id: str | None
    disposition: str
    data: dict
    created_at: datetime
