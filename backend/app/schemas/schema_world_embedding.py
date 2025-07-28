from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class WorldEmbeddingBase(BaseModel):
    world_id: int
    name: str
    collection: str


class WorldEmbeddingCreate(WorldEmbeddingBase):
    pass


class WorldEmbedding(WorldEmbeddingBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True
