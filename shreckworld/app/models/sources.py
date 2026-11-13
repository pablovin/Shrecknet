"""Source organization and immutable template/editor revisions; no graph storage."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class SourceHierarchy(Base):
    __tablename__ = 'source_hierarchies'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default='')
    image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    section_types: Mapped[list[str]] = mapped_column(JSON)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, default='explanatory')
    archived: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceOrganization(Base):
    __tablename__ = 'source_organizations'
    source_id: Mapped[str] = mapped_column(ForeignKey('shreckworld_library_items.id', ondelete='CASCADE'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    nodes: Mapped[list[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceAsset(Base):
    """Protected per-section file. The current row points to an immutable revision."""
    __tablename__ = "source_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey('shreckworld_library_items.id', ondelete='CASCADE'), index=True)
    section_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))  # markdown | image
    storage_path: Mapped[str] = mapped_column(String(1024))
    mime_type: Mapped[str] = mapped_column(String(128))
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("source_id", "section_id", "kind", name="uq_source_section_asset"),)


class GraphSourceImport(Base):
    """One permanent mapping per original graph entity; reruns never replace edits."""
    __tablename__ = "graph_source_imports"
    ontology_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_instance_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_library_items.id", ondelete="CASCADE"))
    section_id: Mapped[str] = mapped_column(String(128))
    source_content_hash: Mapped[str] = mapped_column(String(64))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
