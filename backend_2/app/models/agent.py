"""Agent model for agentic infrastructure."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:  # pragma: no cover
    from app.models.ontology import Ontology


# Association table for many-to-many relationship between agents and ontologies
agent_ontologies = Table(
    "agent_ontologies",
    Base.metadata,
    Column(
        "agent_id",
        String(36),
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "ontology_id",
        Integer,
        ForeignKey("ontologies.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Agent(Base):
    """
    Agent model representing an AI agent that can perform tasks.

    Agents are persisted in SQLite and can be linked to multiple ontologies
    for context-aware task execution.
    """

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4()), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    writing_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    job: Mapped[str] = mapped_column(
        String(50), nullable=False, default="elder", index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationship to ontologies through association table
    ontologies: Mapped[List["Ontology"]] = relationship(
        "Ontology",
        secondary=agent_ontologies,
        back_populates="agents",
    )

    def __repr__(self) -> str:
        return f"<Agent(id={self.id}, name={self.name}, job={self.job}, active={self.active})>"
