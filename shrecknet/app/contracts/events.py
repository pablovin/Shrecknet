from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


EventType = Literal[
    "user.updated",
    "world.updated",
    "ontology.changed",
    "agent.job.completed",
]


class EventEnvelope(BaseModel):
    version: str = "v1"
    event_type: EventType
    event_id: str
    occurred_at: datetime
    source: str = "shrecknet"
    payload: dict[str, Any] = Field(default_factory=dict)
