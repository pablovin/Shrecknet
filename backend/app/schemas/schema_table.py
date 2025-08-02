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

class TableMemberRead(SQLModel):
    table_id: int
    user_id: int
    is_gm: bool = False

TableCreate.model_rebuild()
TableRead.model_rebuild()
TableMemberRead.model_rebuild()
