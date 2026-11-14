"""Isolated, versioned definition graph for Ontology Studio. No Neo4j writes."""
from __future__ import annotations
from datetime import datetime
from uuid import uuid4
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

def uid() -> str:
    return str(uuid4())

class WorldOntology(Base):
    __tablename__ = "shreckworld_ontology"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    ontology_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class OntologyEntity(Base):
    __tablename__ = "shreckworld_ontology_entities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    root_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_ontology.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    aliases: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("shreckworld_ontology_entities.id", ondelete="SET NULL"), nullable=True)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    review_status: Mapped[str] = mapped_column(String(24), default="approved", nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (UniqueConstraint("root_id", "name", name="uq_world_ontology_entity_name"),)

class OntologyProperty(Base):
    __tablename__ = "shreckworld_ontology_properties"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    entity_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_ontology_entities.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    data_type: Mapped[str] = mapped_column(String(40), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cardinality: Mapped[str] = mapped_column(String(16), default="one", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    constraints: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    __table_args__ = (UniqueConstraint("entity_id", "name", name="uq_world_ontology_property_name"),)

class OntologyRelationship(Base):
    __tablename__ = "shreckworld_ontology_relationships"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    root_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_ontology.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_ontology_entities.id", ondelete="CASCADE"))
    target_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_ontology_entities.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cardinality: Mapped[str] = mapped_column(String(16), default="many", nullable=False)
    inverse_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    __table_args__ = (UniqueConstraint("root_id", "source_id", "target_id", "name", name="uq_world_ontology_relationship"),)

class OntologyRevision(Base):
    __tablename__ = "shreckworld_ontology_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    root_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_ontology.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    author_id: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("root_id", "revision", name="uq_world_ontology_revision"),)

class OntologyLearnRun(Base):
    __tablename__ = "shreckworld_ontology_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    ontology_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    input_sources: Mapped[list] = mapped_column(JSON, nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    model_targets: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stage: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    proposals: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    proposal_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    diagnostics: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_ontology_learn_idempotency"),)

class OntologyLearnSource(Base):
    __tablename__ = "shreckworld_ontology_learned_sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    ontology_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_ontology_runs.id"), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("ontology_id", "source_id", name="uq_ontology_learn_source"),)


class OntologyLearnWindow(Base):
    """Validated per-window checkpoint; supports manual reruns with cached output.

    A new run gets its own checkpoint rows even when reusing a previous
    successful result. Failed/incomplete windows are never reused.
    """
    __tablename__ = "shreckworld_ontology_learn_windows"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_ontology_runs.id", ondelete="CASCADE"), index=True)
    ontology_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(200), nullable=False)
    chapter_title: Mapped[str] = mapped_column(String(500), nullable=False)
    window_index: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    contract_version: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    cached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    proposals: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("run_id", "fingerprint", name="uq_ontology_window_per_run"),)

class OntologyLearnChapter(Base):
    """Chapter windows that reached the Architect model for a pinned document."""
    __tablename__ = "shreckworld_ontology_learned_chapters"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    ontology_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(200), nullable=False)
    chapter_title: Mapped[str] = mapped_column(String(500), nullable=False)
    last_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("ontology_id", "source_id", "content_digest", "chapter_id", name="uq_ontology_learn_chapter"),)
