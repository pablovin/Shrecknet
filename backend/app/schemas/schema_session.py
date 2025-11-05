from typing import Optional, List
from datetime import datetime
from sqlmodel import SQLModel
from pydantic import field_validator


class SessionBase(SQLModel):
    name: str
    scheduled_time: Optional[datetime] = None
    summary: Optional[str] = None
    location: Optional[str] = None
    timezone: str

    @field_validator("scheduled_time")
    @classmethod
    def validate_scheduled_time_timezone(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Ensure scheduled_time is timezone-aware if provided."""
        if v is not None and v.tzinfo is None:
            raise ValueError("scheduled_time must include timezone information")
        return v


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

    @field_validator("created_at")
    @classmethod
    def validate_created_at_timezone(cls, v: datetime) -> datetime:
        """Ensure created_at is timezone-aware."""
        if v.tzinfo is None:
            raise ValueError("created_at must include timezone information")
        return v


class SessionUpdate(SQLModel):
    name: Optional[str] = None
    scheduled_time: Optional[datetime] = None
    summary: Optional[str] = None
    location: Optional[str] = None
    timezone: Optional[str] = None
    attendee_ids: Optional[List[int]] = None
    page_ids: Optional[List[int]] = None

    @field_validator("scheduled_time")
    @classmethod
    def validate_scheduled_time_timezone(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Ensure scheduled_time is timezone-aware if provided."""
        if v is not None and v.tzinfo is None:
            raise ValueError("scheduled_time must include timezone information")
        return v


SessionCreate.model_rebuild()
SessionRead.model_rebuild()
SessionUpdate.model_rebuild()
