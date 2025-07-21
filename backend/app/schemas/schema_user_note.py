from typing import Optional, List, Dict
from sqlmodel import SQLModel
from datetime import datetime

class UserNoteBase(SQLModel):
    title: str
    content: Optional[str] = None
    note_date: Optional[datetime] = None
    tags: List[str] = []
    gameworld_id: Optional[int] = None
    shared_with_user_ids: List[int] = []
    contributors: Optional[List[Dict]] = None
    locked_by_user_id: Optional[int] = None
    locked_at: Optional[datetime] = None

class UserNoteCreate(UserNoteBase):
    pass

class UserNoteUpdate(SQLModel):
    title: Optional[str] = None
    content: Optional[str] = None
    note_date: Optional[datetime] = None
    tags: Optional[List[str]] = None
    gameworld_id: Optional[int] = None
    shared_with_user_ids: Optional[List[int]] = None

class UserNoteRead(UserNoteBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: Optional[datetime]

UserNoteCreate.model_rebuild()
UserNoteUpdate.model_rebuild()
UserNoteRead.model_rebuild()
