"""Pydantic schemas for Novelist job."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class NovelistRunCreate(BaseModel):
    """Payload to start a simplified novelist draft job."""

    unstructured_text: str = Field(
        ...,
        min_length=1,
        description="Raw unstructured text to be expanded into a chapter",
    )
    language: Optional[str] = Field(None, description="Target language")
    instructions: Optional[str] = Field(
        None, description="Extra parsing/writing instructions for the novelist"
    )

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
    artifacts: dict[str, Any] | None = None
    draft_text: Optional[str] = None
    critic_notes: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
