from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Column, Enum as SqlEnum, ForeignKey, Integer, String, Table
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
