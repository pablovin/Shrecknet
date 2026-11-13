"""Per-world Narrative/Explanatory hierarchy collections and their editorial entries.

Templates stay global and immutable by version. Collections carry the editable
world-local entries; documents and their Book Maps remain independent Sources.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class SourceCollection(Base):
    __tablename__ = "source_collections"
    __table_args__ = (
        UniqueConstraint("ontology_id", "source_type", "hierarchy_template_id",
                         name="uq_source_collection_world_type_template"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ontology_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    hierarchy_template_id: Mapped[str] = mapped_column(String(36), nullable=False)
    hierarchy_template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    nodes: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
