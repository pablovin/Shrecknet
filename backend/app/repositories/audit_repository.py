from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import Select, select

from app.models.audit import AuditAction, AuditActorType, AuditEntityType, AuditLog
from app.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository):
    async def create(self, data: dict[str, Any]) -> AuditLog:
        log = AuditLog(**data)
        await self.save(log)
        await self.session.flush()
        return log

    async def list(
        self,
        *,
        skip: int = 0,
        limit: int = 50,
        actor_type: AuditActorType | None = None,
        actor_user_id: int | None = None,
        actor_agent_id: str | None = None,
        entity_type: AuditEntityType | None = None,
        action: AuditAction | None = None,
        entity_id: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Sequence[AuditLog]:
        query: Select[tuple[AuditLog]] = select(AuditLog).order_by(
            AuditLog.created_at.desc()
        )
        if actor_type is not None:
            query = query.where(AuditLog.actor_type == actor_type)
        if actor_user_id is not None:
            query = query.where(AuditLog.actor_user_id == actor_user_id)
        if actor_agent_id is not None:
            query = query.where(AuditLog.actor_agent_id == actor_agent_id)
        if entity_type is not None:
            query = query.where(AuditLog.entity_type == entity_type)
        if action is not None:
            query = query.where(AuditLog.action == action)
        if entity_id is not None:
            query = query.where(AuditLog.entity_id == entity_id)
        if start is not None:
            query = query.where(AuditLog.created_at >= start)
        if end is not None:
            query = query.where(AuditLog.created_at <= end)

        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return result.scalars().all()
