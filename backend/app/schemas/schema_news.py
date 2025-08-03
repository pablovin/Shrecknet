from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel


class NewsBase(SQLModel):
    title: str
    type: str
    description: str
    user_id: Optional[int] = None


class NewsCreate(NewsBase):
    pass


class NewsRead(NewsBase):
    id: int
    created_at: datetime
    seen: Optional[bool] = False


NewsCreate.model_rebuild()
NewsRead.model_rebuild()
