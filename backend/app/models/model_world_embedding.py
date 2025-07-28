from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import datetime, timezone

class WorldEmbedding(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    world_id: int = Field(foreign_key="gameworld.id")
    name: str
    collection: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
