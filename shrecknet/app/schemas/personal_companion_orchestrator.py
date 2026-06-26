"""Schemas for Personal Companion Herald Orchestrator endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AllocatedToolAgent(BaseModel):
    """Agent allocation entry returned by world bootstrap."""

    id: str
    name: str
    job: str
    ontology_ids: list[int] = Field(default_factory=list)


class OrchestratorToolAllocation(BaseModel):
    """Allocated tool agents grouped by job type."""

    elder: list[AllocatedToolAgent] = Field(default_factory=list)
    librarian: list[AllocatedToolAgent] = Field(default_factory=list)


class CompanionWorldBootstrapRequest(BaseModel):
    """Request payload for world bootstrap before chat turns."""

    ontology_id: int = Field(..., ge=1)


class CompanionWorldBootstrapResponse(BaseModel):
    """Response payload containing world context and tool allocation."""

    session_id: str
    companion_id: str
    ontology_id: int
    allocated_tools: OrchestratorToolAllocation
    created_at: datetime


class CompanionOrchestratorTurnRequest(BaseModel):
    """Request payload for an orchestrated user turn."""

    query: str = Field(..., min_length=1, max_length=3000)


class CompanionOrchestratorTurnQueuedResponse(BaseModel):
    """Response after queueing a turn as a background job."""

    job_id: int
    status: str
    session_id: str
    ontology_id: int


class CompanionOrchestratorTurnResultResponse(BaseModel):
    """Polled orchestrator result payload for frontend rendering."""

    job_id: int
    status: str
    payload: dict[str, Any] = Field(default_factory=dict)
