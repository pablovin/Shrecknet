"""Revocable browser sessions; only refresh digests are stored unencrypted."""
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    refresh_digest: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[int] = mapped_column(Integer)
    absolute_expires_at: Mapped[int] = mapped_column(Integer, index=True)
    idle_expires_at: Mapped[int] = mapped_column(Integer)
    persistent: Mapped[bool] = mapped_column(Boolean)
    revoked_at: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AuthRotation(Base):
    __tablename__ = "auth_session_rotations"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("auth_sessions.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[str] = mapped_column(String(64))
    rotated_at: Mapped[int] = mapped_column(Integer)
    # Fernet-encrypted replacement, meaningful only in the 30-second retry window.
    replacement: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(Integer, index=True)
