from datetime import datetime
from typing import List, Optional
from sqlmodel import SQLModel


class NewsBase(SQLModel):
    title: str
    type: str
    description: str


class NewsCreate(NewsBase):
    user_ids: List[int] = []


class NewsRead(NewsBase):
    id: int
    created_at: datetime
    seen: Optional[bool] = False


NewsCreate.model_rebuild()
NewsRead.model_rebuild()
