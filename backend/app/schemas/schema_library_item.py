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

class LibraryItemRead(LibraryItemBase):
    id: int
    path: str
    added_at: datetime

    class Config:
        orm_mode = True
