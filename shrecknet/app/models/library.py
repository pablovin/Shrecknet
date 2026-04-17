from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import expression

from app.core.config_store import get_settings
from app.db.base import Base

library_bookmark_shares = Table(
    "library_bookmark_shares",
    Base.metadata,
    Column("bookmark_id", ForeignKey("library_bookmarks.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class LibraryItem(Base):
    __tablename__ = "library_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ontology_id: Mapped[int] = mapped_column(
        ForeignKey("ontologies.id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255))
    authors: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pdf_path: Mapped[str] = mapped_column(String(512))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    vectorized: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=expression.false())
    last_vectorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ontology = relationship("Ontology", back_populates="library_items")
    bookmarks = relationship("LibraryBookmark", back_populates="item", cascade="all, delete-orphan")

    @property
    def pdf_url(self) -> str:
        settings = get_settings()
        base_url = (
            settings.media_public_url.rstrip("/")
            if settings.media_public_url
            else settings.media_base_url.rstrip("/")
        )
        return f"{base_url}/{self.pdf_path}"


class LibraryBookmark(Base):
    __tablename__ = "library_bookmarks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("library_items.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    page: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=expression.true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    item = relationship("LibraryItem", back_populates="bookmarks")
    owner = relationship("User", back_populates="library_bookmarks")
    shared_with = relationship("User", secondary=library_bookmark_shares, back_populates="shared_library_bookmarks")

    __table_args__ = (
        UniqueConstraint("item_id", "owner_id", "title", "page", name="uq_library_bookmarks"),
    )
