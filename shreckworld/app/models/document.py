"""Versioned structured source records; independent of Librarian's graph store."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ShreckWorldDocument(Base):
    __tablename__ = "shreckworld_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    library_item_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_library_items.id", ondelete="CASCADE"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source_sha256: Mapped[str] = mapped_column(String(64))
    parser_name: Mapped[str] = mapped_column(String(32))
    parser_version: Mapped[str] = mapped_column(String(64))
    embedding_version: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str] = mapped_column(String(256))
    embedding_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    embedding_dimension: Mapped[int] = mapped_column(Integer)
    manifest_path: Mapped[str] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ShreckWorldDocumentPage(Base):
    __tablename__ = "shreckworld_document_pages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_documents.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    page_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    width: Mapped[float | None] = mapped_column(nullable=True)
    height: Mapped[float | None] = mapped_column(nullable=True)
    __table_args__ = (UniqueConstraint("document_id", "page_number"),)


class ShreckWorldDocumentSection(Base):
    __tablename__ = "shreckworld_document_sections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_documents.id", ondelete="CASCADE"), index=True)
    parent_section_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    heading: Mapped[str | None] = mapped_column(Text, nullable=True)
    heading_level: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[list[str]] = mapped_column(JSON)


class ShreckWorldDocumentBlock(Base):
    __tablename__ = "shreckworld_document_blocks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_documents.id", ondelete="CASCADE"), index=True)
    section_id: Mapped[str] = mapped_column(String(36), index=True)
    block_type: Mapped[str] = mapped_column(String(32))
    display_text: Mapped[str] = mapped_column(Text)
    reading_order: Mapped[int] = mapped_column(Integer)
    docling_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    block_metadata: Mapped[dict[str, Any]] = mapped_column(JSON)
