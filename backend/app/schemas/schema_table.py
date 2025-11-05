from typing import Optional, List
from datetime import datetime, timezone
from sqlmodel import SQLModel
from pydantic import field_validator


class TableBase(SQLModel):
    world_id: int
    name: str
    crest_url: Optional[str] = None


class TableCreate(TableBase):
    member_ids: List[int] = []


class TableRead(TableBase):
    id: int
    created_by: int
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def ensure_timezone_aware(cls, v: datetime) -> datetime:
        """Ensure created_at is timezone-aware, defaulting to UTC if naive."""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class TableUpdate(SQLModel):
    """Fields allowed for table updates."""

    world_id: Optional[int] = None
    name: Optional[str] = None
    crest_url: Optional[str] = None
    member_ids: Optional[List[int]] = None


class TableMemberRead(SQLModel):
    table_id: int
    user_id: int
    is_gm: bool = False


class TableMemberInfo(SQLModel):
    """Simplified member info for table listings."""

    id: int
    nickname: str
    image_url: Optional[str] = None


class TableListRead(TableRead):
    """Table representation for list views with related data."""

    world_name: str
    members: List[TableMemberInfo] = []
    latest_session: Optional[datetime] = None
    next_session: Optional[datetime] = None

    @field_validator("latest_session", "next_session", mode="before")
    @classmethod
    def ensure_session_times_timezone_aware(
        cls, v: Optional[datetime]
    ) -> Optional[datetime]:
        """Ensure session times are timezone-aware if provided, defaulting to UTC if naive."""
        if v is not None and isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


TableCreate.model_rebuild()
TableRead.model_rebuild()
TableMemberRead.model_rebuild()
TableUpdate.model_rebuild()
TableMemberInfo.model_rebuild()
TableListRead.model_rebuild()
