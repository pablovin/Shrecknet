from typing import Optional, List
from datetime import datetime, timezone
from sqlmodel import SQLModel, Field, Relationship

class Session(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    table_id: int = Field(foreign_key="table.id")
    scheduled_time: datetime
    summary: Optional[str] = None
    location: Optional[str] = None
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    attendances: List["SessionAttendance"] = Relationship(back_populates="session")

class SessionAttendance(SQLModel, table=True):
    session_id: int = Field(foreign_key="session.id", primary_key=True)
    user_id: int = Field(foreign_key="user.id", primary_key=True)
    attending: bool = Field(default=True)

    session: "Session" = Relationship(back_populates="attendances")
