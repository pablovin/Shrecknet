from typing import Optional, List
from sqlmodel import SQLModel
from datetime import datetime

class PageKeyEventBase(SQLModel):
    event_type: str
    event_date: Optional[datetime] = None
    summary: Optional[str] = None
    source_page_id: Optional[int] = None
    related_page_ids: Optional[List[int]] = []
    author_type: str
    author_id: int

class PageKeyEventCreate(PageKeyEventBase):
    page_id: int

class PageKeyEventRead(PageKeyEventBase):
    id: int
    page_id: int
    added_at: datetime
