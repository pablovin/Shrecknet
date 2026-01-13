from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:  # pragma: no cover
    from app.models.user import User


class PageVisit(Base):
    __tablename__ = "page_visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    page_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    visited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    user: Mapped["User"] = relationship("User")

    __table_args__ = (
        Index("ix_page_visits_page_key_visited_at", "page_key", "visited_at"),
        Index("ix_page_visits_user_id_visited_at", "user_id", "visited_at"),
    )


class PageUserVisit(Base):
    __tablename__ = "page_user_visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    page_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    first_visited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_visited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    visit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    user: Mapped["User"] = relationship("User")

    __table_args__ = (
        UniqueConstraint(
            "page_key", "user_id", name="ux_page_user_visits_page_key_user_id"
        ),
        Index("ix_page_user_visits_user_id_page_key", "user_id", "page_key"),
    )


class PageVisitStats(Base):
    __tablename__ = "page_visit_stats"

    page_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    total_visits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_users: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_visited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
