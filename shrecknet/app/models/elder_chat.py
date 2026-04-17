"""Elder chat models for managing user conversations with elder agents."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:  # pragma: no cover
    from app.models.agent import Agent
    from app.models.user import User


class ElderChat(Base):
    """
    ElderChat model representing a conversation thread between a user and an elder agent.

    Users can have up to 10 chats per elder agent, each with their own history.
    """

    __tablename__ = "elder_chats"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4()), index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)  # Hex color
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="elder_chats")
    agent: Mapped["Agent"] = relationship("Agent", back_populates="elder_chats")
    history: Mapped[list["ElderChatHistory"]] = relationship(
        "ElderChatHistory", back_populates="chat", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ElderChat(id={self.id}, user_id={self.user_id}, agent_id={self.agent_id}, name={self.name})>"


class ElderChatHistory(Base):
    """
    ElderChatHistory model representing individual messages in a chat.

    Stores both user queries and agent responses with timestamps.
    """

    __tablename__ = "elder_chat_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("elder_chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # 'user' or 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    chat: Mapped["ElderChat"] = relationship("ElderChat", back_populates="history")

    def __repr__(self) -> str:
        return f"<ElderChatHistory(id={self.id}, chat_id={self.chat_id}, role={self.role})>"
