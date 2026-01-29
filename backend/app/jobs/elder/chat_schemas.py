"""Pydantic schemas for Elder chat API requests and responses."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# Chat History Schemas
class ElderChatHistoryBase(BaseModel):
    """Base schema for chat history."""

    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class ElderChatHistoryCreate(ElderChatHistoryBase):
    """Schema for creating a chat history entry."""

    pass


class ElderChatHistoryResponse(ElderChatHistoryBase):
    """Schema for chat history response."""

    id: int = Field(..., description="History entry ID")
    chat_id: str = Field(..., description="Chat ID")
    created_at: datetime = Field(..., description="Creation timestamp")

    model_config = {"from_attributes": True}


# Chat Schemas
class ElderChatCreate(BaseModel):
    """Schema for creating a new chat."""

    agent_id: str = Field(..., description="Agent ID")
    name: str = Field(..., min_length=1, max_length=100, description="Chat name")
    color: Optional[str] = Field(
        None,
        pattern="^#[0-9A-Fa-f]{6}$",
        description="Hex color code (e.g., #FF5733)",
    )


class ElderChatUpdate(BaseModel):
    """Schema for updating chat metadata."""

    name: Optional[str] = Field(
        None, min_length=1, max_length=100, description="Chat name"
    )
    color: Optional[str] = Field(
        None,
        pattern="^#[0-9A-Fa-f]{6}$",
        description="Hex color code (e.g., #FF5733)",
    )


class ElderChatResponse(BaseModel):
    """Schema for chat response."""

    id: str = Field(..., description="Chat ID")
    user_id: int = Field(..., description="User ID")
    agent_id: str = Field(..., description="Agent ID")
    name: str = Field(..., description="Chat name")
    color: Optional[str] = Field(None, description="Hex color code")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    message_count: Optional[int] = Field(None, description="Number of messages in chat")

    model_config = {"from_attributes": True}


class ElderChatWithHistoryResponse(ElderChatResponse):
    """Schema for chat response with history."""

    history: list[ElderChatHistoryResponse] = Field(
        default_factory=list, description="Chat history"
    )


class ElderChatListResponse(BaseModel):
    """Schema for listing chats."""

    chats: list[ElderChatResponse] = Field(default_factory=list, description="Chats")
    total: int = Field(..., description="Total number of chats")
    limit: int = Field(..., description="Limit used")
    offset: int = Field(..., description="Offset used")
