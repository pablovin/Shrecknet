from sqlmodel import SQLModel, Field
from datetime import datetime, timezone

class AgentEmbedding(SQLModel, table=True):
    agent_id: int = Field(foreign_key="agent.id", primary_key=True)
    embedding_id: int = Field(foreign_key="worldembedding.id", primary_key=True)
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
