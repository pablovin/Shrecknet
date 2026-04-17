from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.contracts.events import EventEnvelope
from app.events.publisher import get_event_publisher
from app.services.users import User

router = APIRouter(prefix="/events", tags=["events"])


class EmitEventRequest(BaseModel):
    event_type: str
    payload: dict


@router.post("/emit")
async def emit_event(req: EmitEventRequest, user: User = Depends(get_current_user)) -> dict[str, str]:
    envelope = EventEnvelope(
        event_type=req.event_type,  # type: ignore[arg-type]
        event_id=str(uuid4()),
        occurred_at=datetime.now(timezone.utc),
        payload={"actor_user_id": user.id, **req.payload},
    )
    await get_event_publisher().publish(envelope)
    return {"status": "published", "event_id": envelope.event_id}
