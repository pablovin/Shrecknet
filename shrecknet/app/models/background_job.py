from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:  # pragma: no cover
    from app.models.architect import ArchitectAnalysisRun


class AuthorType(str, Enum):
    USER = "user"
    AGENT = "agent"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobType(str, Enum):
    GRAPH_LINK_UPDATE = "graph_link_update"
    NEO4J_EMBEDDING = "neo4j_embedding"
    PDF_BOOK_EMBEDDING = "pdf_book_embedding"
    ARCHITECT_ANALYSIS = "architect_analysis"
    ARCHITECT_GENERATION = "architect_generation"
    BACKUP = "backup"
    RESTORE = "restore"
    LEGACY_IMPORT = "legacy_import"
    NOVELIST_DRAFT = "novelist_draft"
    COMPANION_ORCHESTRATOR = "companion_orchestrator"
    ONTOLOGY_INSTANCE_ENTITY_TYPE_CLEAR = "ontology_instance_entity_type_clear"
    ONTOLOGY_INSTANCE_TIMELINE_CLEAR = "ontology_instance_timeline_clear"


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    author_type: Mapped[AuthorType] = mapped_column(String(50), nullable=False)
    author_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Keep kind for old routers while job_type is used by new task plumbing.
    kind: Mapped[str] = mapped_column(String(100), nullable=False, index=True, default="neo4j_embedding")
    job_type: Mapped[JobType] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(
        String(50), nullable=False, default=JobStatus.QUEUED, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    ontology_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    architect_analysis_runs: Mapped[list["ArchitectAnalysisRun"]] = relationship(
        "ArchitectAnalysisRun",
        back_populates="background_job",
        foreign_keys="ArchitectAnalysisRun.background_job_id",
    )
    architect_generation_runs: Mapped[list["ArchitectAnalysisRun"]] = relationship(
        "ArchitectAnalysisRun",
        back_populates="generation_job",
        foreign_keys="ArchitectAnalysisRun.generation_job_id",
    )
