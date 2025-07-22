from typing import Optional, Dict
from sqlmodel import SQLModel
from datetime import datetime

class PageChangeBase(SQLModel):
    change_type: str
    author_type: str
    author_id: int
    values: Optional[Dict] = None

class PageChangeCreate(PageChangeBase):
    page_id: int

class PageChangeRead(PageChangeBase):
    id: int
    page_id: int
    date: datetime
