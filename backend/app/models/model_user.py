from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime, timezone
import enum


class UserRole(str, enum.Enum):
    system_admin = "system admin"
    world_builder = "world builder"
    writer = "writer"
    player = "player"


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nickname: str
    email: str = Field(index=True, unique=True)
    hashed_password: str
    role: UserRole
    image_url: Optional[str] = None
    timezone: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
