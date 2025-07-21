from sqlmodel import SQLModel, Field
from datetime import datetime, timezone

class AgentLibraryItem(SQLModel, table=True):
    agent_id: int = Field(foreign_key="agent.id", primary_key=True)
    item_id: int = Field(foreign_key="libraryitem.id", primary_key=True)
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
