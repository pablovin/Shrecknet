from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditAction, AuditActorType, AuditEntityType, AuditLog
from app.repositories.audit_repository import AuditLogRepository


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = AuditLogRepository(session)

    async def log_action(
        self,
        *,
        actor_type: AuditActorType = AuditActorType.USER,
        actor_user_id: int | None = None,
        actor_agent_id: str | None = None,
        action: AuditAction,
        entity_type: AuditEntityType,
        entity_id: int | None = None,
        payload: dict[str, Any] | None = None,
        description: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> AuditLog:
        log = await self.repository.create(
            {
                "actor_user_id": actor_user_id,
                "actor_type": actor_type,
                "actor_agent_id": actor_agent_id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "payload": payload,
                "description": description,
                "context": context,
            }
        )
        await self.session.commit()
        await self.session.refresh(log)
        return log

    async def list_logs(
        self,
        *,
        skip: int = 0,
        limit: int = 50,
        actor_user_id: int | None = None,
        actor_type: AuditActorType | None = None,
        actor_agent_id: str | None = None,
        entity_type: AuditEntityType | None = None,
        action: AuditAction | None = None,
        entity_id: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Sequence[AuditLog]:
        return await self.repository.list(
            skip=skip,
            limit=limit,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            actor_agent_id=actor_agent_id,
            entity_type=entity_type,
            action=action,
            entity_id=entity_id,
            start=start,
            end=end,
        )
