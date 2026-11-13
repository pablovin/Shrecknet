"""Source organization and immutable template/editor revisions; no graph storage."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
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
    archived: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceOrganization(Base):
    __tablename__ = 'source_organizations'
    source_id: Mapped[str] = mapped_column(ForeignKey('shreckworld_library_items.id', ondelete='CASCADE'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    nodes: Mapped[list[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
