from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import expression

from app.db.base import Base

if TYPE_CHECKING:  # pragma: no cover
    from app.models.ontology import Ontology
    from app.models.user import User


library_bookmark_shares = Table(
    "library_bookmark_shares",
    Base.metadata,
    Column(
        "bookmark_id",
        ForeignKey("library_bookmarks.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "user_id",
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)


class LibraryItem(Base):
    __tablename__ = "library_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ontology_id: Mapped[int] = mapped_column(
        ForeignKey("ontologies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    authors: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pdf_path: Mapped[str] = mapped_column(String(512), nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    vectorized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=expression.false()
    )
    last_vectorized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    ontology: Mapped["Ontology"] = relationship(
        "Ontology", back_populates="library_items"
    )
    bookmarks: Mapped[list["LibraryBookmark"]] = relationship(
        "LibraryBookmark",
        back_populates="item",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class LibraryBookmark(Base):
    __tablename__ = "library_bookmarks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("library_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_private: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=expression.true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    item: Mapped[LibraryItem] = relationship("LibraryItem", back_populates="bookmarks")
    owner: Mapped["User"] = relationship("User", back_populates="library_bookmarks")
    shared_with: Mapped[list["User"]] = relationship(
        "User",
        secondary=library_bookmark_shares,
        back_populates="shared_library_bookmarks",
    )

    __table_args__ = (
        UniqueConstraint(
            "item_id",
            "owner_id",
            "title",
            "page",
            name="uq_library_bookmark_unique_per_page",
        ),
    )
