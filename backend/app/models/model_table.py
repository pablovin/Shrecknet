from typing import Optional, List
from datetime import datetime, timezone
from sqlmodel import SQLModel, Field, Relationship

class Table(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    world_id: int = Field(foreign_key="gameworld.id")
    name: str
    crest_url: Optional[str] = None
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    members: List["TableMember"] = Relationship(back_populates="table")

class TableMember(SQLModel, table=True):
    table_id: int = Field(foreign_key="table.id", primary_key=True)
    user_id: int = Field(foreign_key="user.id", primary_key=True)
    is_gm: bool = Field(default=False)

    table: "Table" = Relationship(back_populates="members")
