from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_audit_service, require_roles
from app.models.audit import AuditAction, AuditActorType, AuditEntityType
from app.models.user import UserRole
from app.schemas.audit import AuditLogRead
from app.services.audit_service import AuditService

router = APIRouter(
    prefix="/logs",
    tags=["audit"],
    dependencies=[Depends(require_roles(UserRole.ADMIN))],
)


def parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("Invalid datetime format. Use ISO 8601.") from exc


@router.get("/", response_model=list[AuditLogRead])
async def list_audit_logs(
    audit_service: AuditService = Depends(get_audit_service),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, gt=0, le=200),
    actor_type: AuditActorType | None = None,
    actor_user_id: int | None = None,
    actor_agent_id: str | None = None,
    entity_type: AuditEntityType | None = None,
    action: AuditAction | None = None,
    entity_id: int | None = None,
    start: str | None = Query(None, description="ISO start datetime"),
    end: str | None = Query(None, description="ISO end datetime"),
) -> list[AuditLogRead]:
    try:
        parsed_start = parse_optional_datetime(start)
        parsed_end = parse_optional_datetime(end)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    logs = await audit_service.list_logs(
        skip=skip,
        limit=limit,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        actor_agent_id=actor_agent_id,
        entity_type=entity_type,
        action=action,
        entity_id=entity_id,
        start=parsed_start,
        end=parsed_end,
    )
    return [AuditLogRead.model_validate(log) for log in logs]
