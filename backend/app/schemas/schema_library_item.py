from typing import Optional
from sqlmodel import SQLModel
from datetime import datetime

class LibraryItemBase(SQLModel):
    name: str
    system: str
    description: Optional[str] = None

class LibraryItemCreate(LibraryItemBase):
    pass

class LibraryItemUpdate(SQLModel):
    name: Optional[str] = None
    system: Optional[str] = None
    description: Optional[str] = None
    cover_url: Optional[str] = None

class LibraryItemRead(LibraryItemBase):
    id: int
    path: str
    cover_url: Optional[str] = None
    added_at: datetime
    vector_db_update_date: Optional[datetime] = None

    class Config:
        orm_mode = True
