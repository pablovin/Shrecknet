from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from datetime import datetime

from sqlalchemy import Column, DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:  # pragma: no cover
    from app.models.elder_chat import ElderChat
    from app.models.library import LibraryBookmark
    from app.models.ontology import OntologyEntity


class UserRole(str, Enum):
    ADMIN = "admin"
    WORLD_BUILDER = "world_builder"
    WRITER = "writer"
    PLAYER = "player"


class UserApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


def _enum_values(enum_class: type[Enum]) -> list[str]:
    """Persist enum values, rather than Python member names, in the database."""
    return [member.value for member in enum_class]


user_entities = Table(
    "user_entities",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "entity_id",
        ForeignKey("ontology_entities.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(
        String(150), unique=True, nullable=False, index=True, default=""
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Legacy compatibility during migration split.
    password: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC")
    role: Mapped[UserRole] = mapped_column(
        SqlEnum(UserRole), nullable=False, default=UserRole.PLAYER
    )
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    approval_status: Mapped[UserApprovalStatus] = mapped_column(
        SqlEnum(UserApprovalStatus, values_callable=_enum_values),
        nullable=False,
        default=UserApprovalStatus.APPROVED,
    )
    approval_decided_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approval_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    entities: Mapped[list["OntologyEntity"]] = relationship(
        "OntologyEntity",
        secondary=user_entities,
        back_populates="players",
    )
    library_bookmarks: Mapped[list["LibraryBookmark"]] = relationship(
        "LibraryBookmark", back_populates="owner"
    )
    shared_library_bookmarks: Mapped[list["LibraryBookmark"]] = relationship(
        "LibraryBookmark",
        secondary="library_bookmark_shares",
        back_populates="shared_with",
    )
    elder_chats: Mapped[list["ElderChat"]] = relationship(
        "ElderChat", back_populates="user"
    )
