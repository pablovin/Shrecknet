from typing import Optional, List, Dict
from sqlmodel import SQLModel, Field, JSON
from sqlalchemy import Column
from datetime import datetime, timezone

class UserNote(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    title: str
    content: Optional[str] = None
    note_date: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))
    tags: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    gameworld_id: Optional[int] = Field(default=None, foreign_key="gameworld.id")
    shared_with_user_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))
    contributors: List[Dict] = Field(default_factory=list, sa_column=Column(JSON))
    locked_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    locked_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = None
