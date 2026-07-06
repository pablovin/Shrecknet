from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PersonalCompanionAgentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    avatar_url: str | None = Field(None, max_length=512)
    writing_style: str = Field(..., min_length=1)
    active: bool = True


class PersonalCompanionAgentCreate(PersonalCompanionAgentBase):
    pass


class PersonalCompanionAgentUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    avatar_url: str | None = Field(None, max_length=512)
    writing_style: str | None = Field(None, min_length=1)
    active: bool | None = None


class PersonalCompanionAgentRead(PersonalCompanionAgentBase):
    id: str
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AllocatedToolAgent(BaseModel):
    id: str
    name: str
    job: str
    ontology_ids: list[int] = Field(default_factory=list)


class OrchestratorToolAllocation(BaseModel):
    elder: list[AllocatedToolAgent] = Field(default_factory=list)
    librarian: list[AllocatedToolAgent] = Field(default_factory=list)


class CompanionWorldBootstrapRequest(BaseModel):
    ontology_id: int = Field(..., ge=1)


class CompanionWorldBootstrapResponse(BaseModel):
    companion_id: str
    ontology_id: int
    allocated_tools: OrchestratorToolAllocation
    existing_chat_count: int
    chat_limit: int


class CompanionChatSessionCreateRequest(BaseModel):
    ontology_id: int = Field(..., ge=1)
    title: str | None = Field(None, min_length=1, max_length=255)


class CompanionChatSessionUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


class CompanionChatSessionRead(BaseModel):
    session_id: str
    companion_id: str
    ontology_id: int
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None


class CompanionChatSessionCount(BaseModel):
    ontology_id: int
    count: int
    limit: int


class CompanionOrchestratorTurnRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=3000)


class CompanionOrchestratorTurnQueuedResponse(BaseModel):
    job_id: int
    status: str
    session_id: str
    ontology_id: int


class CompanionOrchestratorTurnResultResponse(BaseModel):
    job_id: int
    status: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ServiceStatusResponse(BaseModel):
    service: str
    status: str
    database_path: str
    shreckllm_base_url: str
    shrecknet_api_base_url: str
    active_jobs: int
