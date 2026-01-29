from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.audit import AuditAction, AuditActorType, AuditEntityType


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    actor_type: AuditActorType
    actor_user_id: int | None = None
    actor_agent_id: str | None = None
    action: AuditAction
    entity_type: AuditEntityType
    entity_id: int | None = None
    description: str | None = None
    payload: dict | None = None
    context: dict | None = None


class AuditLogListParams(BaseModel):
    skip: int = Field(0, ge=0)
    limit: int = Field(50, gt=0, le=100)
    actor_type: AuditActorType | None = None
    actor_user_id: int | None = None
    actor_agent_id: str | None = None
    entity_type: AuditEntityType | None = None
    action: AuditAction | None = None
    entity_id: int | None = None
    start: datetime | None = None
    end: datetime | None = None
