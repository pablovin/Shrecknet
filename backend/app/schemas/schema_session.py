from typing import Optional, List
from datetime import datetime
from sqlmodel import SQLModel

class SessionBase(SQLModel):
    scheduled_time: datetime
    summary: Optional[str] = None
    location: Optional[str] = None

class SessionCreate(SessionBase):
    table_id: int
    attendee_ids: List[int] = []

class SessionRead(SessionBase):
    id: int
    table_id: int
    created_by: int
    created_at: datetime

SessionCreate.model_rebuild()
SessionRead.model_rebuild()
