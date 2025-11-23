"""Pydantic schemas for Novelist job."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NovelistSource(BaseModel):
    """Input source containing unstructured text."""

    kind: Literal["text", "file_path"] = Field(
        "text", description="text for raw content, file_path for server-side file"
    )
    content: Optional[str] = Field(
        None, description="Raw text when kind=text; ignored otherwise"
    )
    path: Optional[str] = Field(
        None, description="Server-side absolute/relative file path when kind=file_path"
    )
    label: Optional[str] = Field(None, description="Friendly name for UI")

    @field_validator("content", mode="after")
    def _require_content(cls, v, info):  # pragma: no cover - simple validation
        kind = info.data.get("kind")
        if kind == "text" and not v:
            raise ValueError("content is required when kind='text'")
        return v


class NovelistRunCreate(BaseModel):
    """Payload to start a novelist draft job (step 1)."""

    sources: list[NovelistSource] = Field(..., min_length=1)
    language: Optional[str] = Field(None, description="Target language override")
    instructions: Optional[str] = Field(
        None, description="User-supplied instructions (characters, style, etc.)"
    )
    chunk_size: int = Field(
        2000, description="Words per chunk before novelizing", ge=200, le=6000
    )
    max_chunks: int = Field(4, ge=1, le=20)
    questions_per_chunk: int = Field(10, ge=1, le=20)
    elder_agent_id: Optional[str] = Field(
        None, description="Helper elder agent used to answer context questions"
    )
    novelist_prompt: Optional[str] = Field(
        None, description="Custom prompt override for chunk novelization"
    )
    critic_prompt: Optional[str] = Field(
        None, description="Custom prompt override for critic pass"
    )
    ontology_id: Optional[int] = Field(None, description="Optional ontology scope")
    ontology_instance_id: Optional[str] = Field(
        None, description="Optional ontology instance scope"
    )


class NovelistChunkResult(BaseModel):
    """Per-chunk status and outputs."""

    index: int
    source_label: Optional[str] = None
    raw_preview: str | None = None
    questions: list[str] = Field(default_factory=list)
    answers: list[str] = Field(default_factory=list)
    draft: str | None = None
    status: str = "pending"


class NovelistRunRead(BaseModel):
    """Response representing a Novelist run."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    background_job_id: Optional[int] = None
    ontology_id: Optional[int] = None
    ontology_instance_id: Optional[str] = None
    status: str
    stage: str
    settings: dict[str, Any] | None = None
    request_payload: dict[str, Any] | None = None
    chunks: list[dict[str, Any]] | None = None
    draft_text: Optional[str] = None
    critic_notes: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
