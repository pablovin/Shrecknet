"""Durable Character Companion records; graph identities remain in Neo4j."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


def utcnow():
    return datetime.now(timezone.utc)


def uuid():
    return str(uuid4())


class CompanionSelection(Base):
    __tablename__ = "character_companion_selections"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    character_agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[int] = mapped_column(default=0)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanionRapport(Base):
    __tablename__ = "character_companion_rapports"
    __table_args__ = tuple(CheckConstraint(f"{key} BETWEEN 0 AND 100") for key in
                          ("connection", "coordination", "trust", "familiarity", "openness", "current_strain"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    character_agent_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    connection: Mapped[int] = mapped_column(default=25)
    coordination: Mapped[int] = mapped_column(default=20)
    trust: Mapped[int] = mapped_column(default=20)
    familiarity: Mapped[int] = mapped_column(default=0)
    openness: Mapped[int] = mapped_column(default=10)
    current_strain: Mapped[int] = mapped_column(default=0)
    version: Mapped[int] = mapped_column(default=0)
    generation: Mapped[int] = mapped_column(default=0)
    next_sequence: Mapped[int] = mapped_column(default=0)
    evaluated_sequence: Mapped[int] = mapped_column(default=0)
    interaction_count: Mapped[int] = mapped_column(default=0)
    last_turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanionChat(Base):
    __tablename__ = "character_companion_chats"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    character_agent_id: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(255))
    default_scope: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    revision: Mapped[int] = mapped_column(default=1)
    active_turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    deleted: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanionTurn(Base):
    __tablename__ = "character_companion_turns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    chat_id: Mapped[str] = mapped_column(ForeignKey("character_companion_chats.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    character_agent_id: Mapped[str] = mapped_column(String(255))
    request: Mapped[dict] = mapped_column(JSON)
    principal: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    draft: Mapped[str] = mapped_column(Text, default="")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    identity: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    generation: Mapped[int] = mapped_column(default=0)
    completion_sequence: Mapped[int | None] = mapped_column(nullable=True)
    rapport_status: Mapped[str] = mapped_column(String(32), default="not_applicable")
    lease: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanionMessage(Base):
    __tablename__ = "character_companion_messages"
    __table_args__ = (UniqueConstraint("turn_id", "role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    turn_id: Mapped[str] = mapped_column(ForeignKey("character_companion_turns.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("character_companion_chats.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    manifest: Mapped[list] = mapped_column(JSON, default=list)
    committed: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanionEvent(Base):
    __tablename__ = "character_companion_events"
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanionOutbox(Base):
    __tablename__ = "character_companion_outbox"
    __table_args__ = (UniqueConstraint("kind", "aggregate_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    kind: Mapped[str] = mapped_column(String(24))
    aggregate_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanionRapportAudit(Base):
    __tablename__ = "character_companion_rapport_audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    character_agent_id: Mapped[str] = mapped_column(String(255), index=True)
    turn_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    disposition: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanionAttachment(Base):
    __tablename__ = "character_companion_attachments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(36))
    size: Mapped[int] = mapped_column()
    width: Mapped[int] = mapped_column()
    height: Mapped[int] = mapped_column()
    sha256: Mapped[str] = mapped_column(String(64))
    deleted: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanionIdempotency(Base):
    __tablename__ = "character_companion_idempotency"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    operation: Mapped[str] = mapped_column(String(100), primary_key=True)
    key: Mapped[str] = mapped_column(String(36), primary_key=True)
    body_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)


class CompanionImport(Base):
    __tablename__ = "character_companion_imports"
    source_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64))
    destination_id: Mapped[str] = mapped_column(String(255))
