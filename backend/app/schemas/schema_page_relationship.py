from typing import Optional
from sqlmodel import SQLModel
from datetime import datetime

class PageRelationshipBase(SQLModel):
    target_page_id: int
    relationship_type: str
    direction: str = "outgoing"
    source_page_id: Optional[int] = None
    description: Optional[str] = None
    author_type: str
    author_id: int

class PageRelationshipCreate(PageRelationshipBase):
    page_id: int

class PageRelationshipRead(PageRelationshipBase):
    id: int
    page_id: int
    added_at: datetime

class PageRelationshipUpdate(SQLModel):
    target_page_id: Optional[int] = None
    relationship_type: Optional[str] = None
    direction: Optional[str] = None
    source_page_id: Optional[int] = None
    description: Optional[str] = None
