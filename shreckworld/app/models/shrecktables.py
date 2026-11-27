"""Canonical ShreckTables records.

The old game-engine shreck_tables and ShreckRPG games are migration inputs only.
No cross-service foreign keys are created for Shrecknet-owned user/world IDs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


def new_id() -> str:
    return str(uuid4())


class Table(Base):
    __tablename__ = "shrecktables"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ontology_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Retained for legacy migration. Live rules are selected on the world.
    version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    timezone: Mapped[str] = mapped_column(String(100), default="UTC", nullable=False)
    vtt_url: Mapped[str | None] = mapped_column(String(1024))
    calendar_id: Mapped[str | None] = mapped_column(String(255))
    google_connection_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    auto_transcript: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_notes: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    memberships: Mapped[list["TableMember"]] = relationship(back_populates="table", cascade="all, delete-orphan")
    sessions: Mapped[list["TableSession"]] = relationship(back_populates="table", cascade="all, delete-orphan")


class TableMember(Base):
    __tablename__ = "shrecktables_members"
    __table_args__ = (UniqueConstraint("table_id", "user_id", name="uq_shrecktables_member"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("shrecktables.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16), default="player", nullable=False)
    character_agent_id: Mapped[str | None] = mapped_column(String(64))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    table: Mapped[Table] = relationship(back_populates="memberships")


class TableSession(Base):
    __tablename__ = "shrecktables_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("shrecktables.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))
    scheduled_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_timezone: Mapped[str] = mapped_column(String(100), default="UTC", nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=180, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    google_event_id: Mapped[str | None] = mapped_column(String(255))
    google_event_calendar_id: Mapped[str | None] = mapped_column(String(255))
    google_event_connection_id: Mapped[str | None] = mapped_column(String(36))
    google_meet_url: Mapped[str | None] = mapped_column(String(1024))
    meet_space: Mapped[str | None] = mapped_column(String(255))
    calendar_sync_status: Mapped[str] = mapped_column(String(24), default="not_configured", nullable=False)
    calendar_sync_error: Mapped[str | None] = mapped_column(String(255))
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    table: Mapped[Table] = relationship(back_populates="sessions")
    polls: Mapped[list["TablePoll"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    attendance: Mapped[list["TableAttendance"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    attachments: Mapped[list["TableAttachment"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class TablePoll(Base):
    __tablename__ = "shrecktables_polls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("shrecktables_sessions.id", ondelete="CASCADE"), index=True)
    is_finalized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    finalized_option_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    session: Mapped[TableSession] = relationship(back_populates="polls")
    options: Mapped[list["TablePollOption"]] = relationship(back_populates="poll", cascade="all, delete-orphan")


class TablePollOption(Base):
    __tablename__ = "shrecktables_poll_options"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[int] = mapped_column(ForeignKey("shrecktables_polls.id", ondelete="CASCADE"), index=True)
    proposed_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    poll: Mapped[TablePoll] = relationship(back_populates="options")
    votes: Mapped[list["TablePollVote"]] = relationship(back_populates="option", cascade="all, delete-orphan")


class TablePollVote(Base):
    __tablename__ = "shrecktables_poll_votes"
    __table_args__ = (UniqueConstraint("option_id", "user_id", name="uq_shrecktables_vote"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    option_id: Mapped[int] = mapped_column(ForeignKey("shrecktables_poll_options.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    option: Mapped[TablePollOption] = relationship(back_populates="votes")


class TableAttendance(Base):
    __tablename__ = "shrecktables_attendance"
    __table_args__ = (UniqueConstraint("session_id", "user_id", name="uq_shrecktables_attendance"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("shrecktables_sessions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attending: Mapped[bool] = mapped_column(Boolean, nullable=False)
    responded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    session: Mapped[TableSession] = relationship(back_populates="attendance")


class TableAttachment(Base):
    __tablename__ = "shrecktables_attachments"
    __table_args__ = (UniqueConstraint("session_id", "source", "external_id", name="uq_shrecktables_import"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("shrecktables_sessions.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    byte_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="upload", nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    original_url: Mapped[str | None] = mapped_column(String(1024))
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    session: Mapped[TableSession] = relationship(back_populates="attachments")


class GoogleConnection(Base):
    __tablename__ = "shrecktables_google_connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="connected", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GoogleOAuthState(Base):
    __tablename__ = "shrecktables_google_oauth_states"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LegacyTableMap(Base):
    __tablename__ = "shrecktables_legacy_map"
    __table_args__ = (UniqueConstraint("source", "old_id", name="uq_shrecktables_legacy_source"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    old_id: Mapped[str] = mapped_column(String(128), nullable=False)
    new_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)


class SessionNarrativeSource(Base):
    __tablename__ = "shrecktables_session_narrative_sources"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("shrecktables_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    attachment_id: Mapped[int | None] = mapped_column(Integer)
    promoted_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
