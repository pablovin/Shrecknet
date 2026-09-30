from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _id() -> str:
    return str(uuid4())


class ShreckWorld(Base):
    __tablename__ = "shreckworlds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    shrecknet_world_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    web_search_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    web_search_policy: Mapped[str] = mapped_column(String(32), default="disabled", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    library_items: Mapped[list["ShreckWorldLibraryItem"]] = relationship(
        back_populates="shreckworld", cascade="all, delete-orphan"
    )


class ShreckWorldLibraryItem(Base):
    __tablename__ = "shreckworld_library_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    shreckworld_id: Mapped[str] = mapped_column(ForeignKey("shreckworlds.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    authors: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    authority_tier: Mapped[str] = mapped_column(String(32), default="core_rules", nullable=False)
    authority_priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cover_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pdf_path: Mapped[str] = mapped_column(String(512), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_status: Mapped[str] = mapped_column(String(32), default="not_embedded", nullable=False, index=True)
    # `index_*` is the public lifecycle terminology.  Legacy embedding columns
    # remain during the prototype migration and are populated in lockstep.
    index_status: Mapped[str] = mapped_column(String(32), default="unindexed", nullable=False, index=True)
    index_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    shreckworld: Mapped[ShreckWorld] = relationship(back_populates="library_items")
    chunks: Mapped[list["ShreckWorldDocumentChunk"]] = relationship(
        back_populates="library_item", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("shreckworld_id", "title", name="uq_shreckworld_library_title"),)


class ShreckWorldDocumentChunk(Base):
    __tablename__ = "shreckworld_document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    library_item_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_library_items.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    library_item: Mapped[ShreckWorldLibraryItem] = relationship(back_populates="chunks")

    __table_args__ = (UniqueConstraint("library_item_id", "ordinal", name="uq_shreckworld_chunk_ordinal"),)


class ShreckWorldEmbeddingJob(Base):
    __tablename__ = "shreckworld_embedding_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    library_item_id: Mapped[str] = mapped_column(ForeignKey("shreckworld_library_items.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
