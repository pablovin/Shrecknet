from typing import Optional, List
from datetime import datetime
from sqlmodel import SQLModel


class SessionBase(SQLModel):
    name: str
    scheduled_time: Optional[datetime] = None
    summary: Optional[str] = None
    location: Optional[str] = None
    timezone: str


class SessionCreate(SessionBase):
    table_id: Optional[int] = None
    attendee_ids: List[int] = []
    page_ids: List[int] = []


class SessionRead(SessionBase):
    id: int
    table_id: int
    created_by: int
    created_at: datetime
    page_ids: List[int] = []


SessionCreate.model_rebuild()
SessionRead.model_rebuild()
