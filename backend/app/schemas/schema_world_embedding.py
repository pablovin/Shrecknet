from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class WorldEmbeddingBase(BaseModel):
    world_id: int
    name: str
    collection: str


class WorldEmbeddingCreate(WorldEmbeddingBase):
    pass


class WorldEmbeddingUpdate(BaseModel):
    world_id: Optional[int] = None
    name: Optional[str] = None
    collection: Optional[str] = None


class WorldEmbedding(WorldEmbeddingBase):
    id: int
    created_at: datetime
    last_index_time: Optional[datetime] = None
    page_count: Optional[int] = None
    build_seconds: Optional[float] = None

    class Config:
        orm_mode = True
