from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _id() -> str:
    return str(uuid4())


class GameRuleset(Base):
    __tablename__ = "game_engine_rulesets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    edition: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    definition_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    definitions: Mapped[list[GameDefinition]] = relationship(back_populates="ruleset", cascade="all, delete-orphan")


class GameBinding(Base):
    __tablename__ = "game_engine_bindings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    ontology_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    ruleset_id: Mapped[str] = mapped_column(ForeignKey("game_engine_rulesets.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("ontology_id", "ruleset_id", name="uq_game_engine_binding_ontology_ruleset"),)


class GameDefinition(Base):
    __tablename__ = "game_engine_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    ruleset_id: Mapped[str] = mapped_column(ForeignKey("game_engine_rulesets.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    provenance: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ruleset: Mapped[GameRuleset] = relationship(back_populates="definitions")


class GameRulesetVersion(Base):
    __tablename__ = "game_engine_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    ruleset_id: Mapped[str] = mapped_column(ForeignKey("game_engine_rulesets.id", ondelete="RESTRICT"), nullable=False, index=True)
    definition_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    definition_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    definitions_snapshot: Mapped[list] = mapped_column(JSON, nullable=False)
    dependency_pins: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="published", nullable=False, index=True)
    published_by: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    supersedes_version_id: Mapped[str | None] = mapped_column(ForeignKey("game_engine_versions.id"), nullable=True)
    __table_args__ = (UniqueConstraint("ruleset_id", "definition_revision", name="uq_game_engine_version_revision"),)


class GameRulesetReview(Base):
    __tablename__ = "game_engine_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    ruleset_id: Mapped[str] = mapped_column(ForeignKey("game_engine_rulesets.id", ondelete="CASCADE"), nullable=False, index=True)
    definition_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GameGenerationRun(Base):
    __tablename__ = "game_engine_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    ruleset_id: Mapped[str] = mapped_column(ForeignKey("game_engine_rulesets.id", ondelete="CASCADE"), nullable=False, index=True)
    binding_id: Mapped[str | None] = mapped_column(ForeignKey("game_engine_bindings.id", ondelete="SET NULL"), nullable=True)
    ontology_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    input_sources: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    retry_of_id: Mapped[str | None] = mapped_column(ForeignKey("game_engine_runs.id"), nullable=True)
    diagnostics: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_game_engine_run_idempotency"),)


class ShreckTable(Base):
    __tablename__ = "shreck_tables"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    ontology_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("game_engine_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    participants: Mapped[list[ShreckTableParticipant]] = relationship(back_populates="table", cascade="all, delete-orphan")


class ShreckTableParticipant(Base):
    __tablename__ = "shreck_table_participants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    table_id: Mapped[str] = mapped_column(ForeignKey("shreck_tables.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    character_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    table: Mapped[ShreckTable] = relationship(back_populates="participants")
    __table_args__ = (UniqueConstraint("table_id", "user_id", name="uq_shreck_table_participant"),)
