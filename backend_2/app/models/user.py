from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Column, Enum as SqlEnum, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:  # pragma: no cover
    from app.models.game import Game, GameSessionAttendance, GameSessionPollVote
    from app.models.library import LibraryBookmark
    from app.models.note import Note
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
        String(150), unique=True, nullable=False, index=True
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[UserRole] = mapped_column(SqlEnum(UserRole), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    entities: Mapped[list["OntologyEntity"]] = relationship(
        secondary=user_entities, back_populates="players",
    )
    games: Mapped[list["Game"]] = relationship(
        "Game", secondary="game_members", back_populates="members",
    )
    session_attendance: Mapped[list["GameSessionAttendance"]] = relationship(
        "GameSessionAttendance", back_populates="user"
    )
    session_poll_votes: Mapped[list["GameSessionPollVote"]] = relationship(
        "GameSessionPollVote", back_populates="user"
    )
    library_bookmarks: Mapped[list["LibraryBookmark"]] = relationship(
        "LibraryBookmark", back_populates="owner"
    )
    shared_library_bookmarks: Mapped[list["LibraryBookmark"]] = relationship(
        "LibraryBookmark",
        secondary="library_bookmark_shares",
        back_populates="shared_with",
    )
    notes: Mapped[list["Note"]] = relationship("Note", back_populates="owner")
    shared_notes: Mapped[list["Note"]] = relationship(
        "Note", secondary="note_shares", back_populates="shared_with"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"User(id={self.id!r}, username={self.username!r}, role={self.role!r})"
