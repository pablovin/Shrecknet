from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import datetime, timezone

class LibraryItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    system: str
    description: Optional[str] = None
    path: str
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
