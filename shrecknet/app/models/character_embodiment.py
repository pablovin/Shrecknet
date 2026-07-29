"""SQL review state for asynchronous CharacterAgent embodiment generation."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CharacterEmbodimentDraftStatus(str, Enum):
    QUEUED = "queued"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    ACCEPTED = "accepted"


class CharacterEmbodimentDraft(Base):
    __tablename__ = "character_embodiment_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ontology_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_entity_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    active_entity_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    target_character_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[CharacterEmbodimentDraftStatus] = mapped_column(
        String(32), nullable=False, default=CharacterEmbodimentDraftStatus.QUEUED, index=True
    )
    background_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    generation_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    evidence_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_evidence_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_cutoff: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_proposal: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeline_projection: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CharacterEmbodimentCheckpoint(Base):
    """Validated, resumable output from one source-analysis stage."""

    __tablename__ = "character_embodiment_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "draft_id", "generation_revision", "source_index", "stage",
            name="uq_character_embodiment_checkpoint_stage",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    draft_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    generation_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_target: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )
