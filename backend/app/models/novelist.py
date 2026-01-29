"""Models for Novelist job runs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class NovelistRunStatus(str, Enum):
    """Lifecycle state for a novelist run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class NovelistStage(str, Enum):
    """Progress markers for the multi-step pipeline."""

    INGEST = "ingest"
    QUESTIONS = "questions"
    ANSWERS = "answers"
    DRAFTING = "drafting"
    MERGING = "merging"
    CRITIC = "critic"
    DONE = "done"


class NovelistRun(Base):
    """Persisted Novelist job execution metadata."""

    __tablename__ = "novelist_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4()), index=True
    )
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    background_job_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("background_jobs.id", ondelete="SET NULL"), nullable=True
    )
    ontology_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    ontology_instance_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    status: Mapped[NovelistRunStatus] = mapped_column(
        SqlEnum(NovelistRunStatus), default=NovelistRunStatus.PENDING, nullable=False
    )
    stage: Mapped[NovelistStage] = mapped_column(
        SqlEnum(NovelistStage), default=NovelistStage.INGEST, nullable=False
    )
    settings: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    chunks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    draft_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    critic_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<NovelistRun(id={self.id}, status={self.status}, stage={self.stage})>"
