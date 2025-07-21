from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import datetime, timezone

class LibraryItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    system: str
    description: Optional[str] = None
    path: str
    cover_url: Optional[str] = None
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    vector_db_update_date: Optional[datetime] = None
