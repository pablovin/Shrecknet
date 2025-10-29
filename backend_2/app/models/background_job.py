"""Background job model for tracking async task execution."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuthorType(str, Enum):
    """Type of author that created the job."""

    USER = "user"
    AGENT = "agent"


class JobStatus(str, Enum):
    """Status of a background job."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobType(str, Enum):
    """Type of background job."""

    GRAPH_LINK_UPDATE = "graph_link_update"
    NEO4J_EMBEDDING = "neo4j_embedding"


class BackgroundJob(Base):
    """
    Background job tracking for async tasks.

    This model persists information about long-running background tasks
    executed via Celery, allowing monitoring of job status and progress.
    """

    __tablename__ = "background_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    celery_task_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    author_type: Mapped[AuthorType] = mapped_column(String(50), nullable=False)
    author_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    job_type: Mapped[JobType] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(
        String(50), nullable=False, default=JobStatus.QUEUED, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # JSON string for additional details
    progress: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0.0 to 1.0
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<BackgroundJob(id={self.id}, type={self.job_type}, "
            f"status={self.status}, progress={self.progress})>"
        )
