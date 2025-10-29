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
    from app.models.notification import Notification
    from app.models.ontology import Ontology
    from app.models.user import User


game_members = Table(
    "game_members",
    Base.metadata,
    Column("game_id", ForeignKey("games.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ontology_id: Mapped[int] = mapped_column(
        ForeignKey("ontologies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    ontology: Mapped["Ontology"] = relationship("Ontology")
    members: Mapped[list["User"]] = relationship(
        "User",
        secondary=game_members,
        back_populates="games",
    )
    sessions: Mapped[list["GameSession"]] = relationship(
        "GameSession",
        back_populates="game",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class GameSession(Base):
    __tablename__ = "game_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    scheduled_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    game: Mapped[Game] = relationship("Game", back_populates="sessions")
    attendance: Mapped[list["GameSessionAttendance"]] = relationship(
        "GameSessionAttendance",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    polls: Mapped[list["GameSessionPoll"]] = relationship(
        "GameSessionPoll",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class GameSessionAttendance(Base):
    __tablename__ = "game_session_attendance"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    attending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=expression.true()
    )
    responded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[GameSession] = relationship(
        "GameSession", back_populates="attendance"
    )
    user: Mapped["User"] = relationship("User", back_populates="session_attendance")


class GameSessionPoll(Base):
    __tablename__ = "game_session_polls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_finalized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=expression.false()
    )
    finalized_option_id: Mapped[int | None] = mapped_column(
        ForeignKey("game_session_poll_options.id", ondelete="SET NULL"),
        nullable=True,
    )

    session: Mapped[GameSession] = relationship("GameSession", back_populates="polls")
    options: Mapped[list["GameSessionPollOption"]] = relationship(
        "GameSessionPollOption",
        back_populates="poll",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="GameSessionPollOption.poll_id",
    )
    finalized_option: Mapped["GameSessionPollOption | None"] = relationship(
        "GameSessionPollOption",
        foreign_keys=[finalized_option_id],
        post_update=True,
    )


class GameSessionPollOption(Base):
    __tablename__ = "game_session_poll_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_id: Mapped[int] = mapped_column(
        ForeignKey("game_session_polls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    proposed_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    poll: Mapped[GameSessionPoll] = relationship(
        "GameSessionPoll", back_populates="options", foreign_keys=[poll_id]
    )
    votes: Mapped[list["GameSessionPollVote"]] = relationship(
        "GameSessionPollVote",
        back_populates="option",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def vote_count(self) -> int:
        return len(self.votes)


class GameSessionPollVote(Base):
    __tablename__ = "game_session_poll_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    option_id: Mapped[int] = mapped_column(
        ForeignKey("game_session_poll_options.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    option: Mapped[GameSessionPollOption] = relationship(
        "GameSessionPollOption", back_populates="votes"
    )
    user: Mapped["User"] = relationship("User", back_populates="session_poll_votes")

    __table_args__ = (
        UniqueConstraint(
            "option_id",
            "user_id",
            name="uq_poll_option_vote",
        ),
    )
