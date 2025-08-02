from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import datetime, timezone

class News(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    type: str
    description: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class NewsView(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    news_id: int = Field(foreign_key="news.id")
    user_id: int = Field(foreign_key="user.id")
    seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
