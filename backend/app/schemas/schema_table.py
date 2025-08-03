from typing import Optional, List
from datetime import datetime
from sqlmodel import SQLModel


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


class TableUpdate(SQLModel):
    """Fields allowed for table updates."""

    world_id: Optional[int] = None
    name: Optional[str] = None
    crest_url: Optional[str] = None


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


TableCreate.model_rebuild()
TableRead.model_rebuild()
TableMemberRead.model_rebuild()
TableUpdate.model_rebuild()
TableMemberInfo.model_rebuild()
TableListRead.model_rebuild()
