"""Pydantic schemas for PersonalCompanionAgent model."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PersonalCompanionAgentBase(BaseModel):
    """Base schema for personal companion agent payloads."""

    name: str = Field(..., min_length=1, max_length=255)
    avatar_url: str | None = Field(None, max_length=512)
    writing_style: str = Field(..., min_length=1)
    active: bool = True


class PersonalCompanionAgentCreate(PersonalCompanionAgentBase):
    """Schema for creating a personal companion agent."""


class PersonalCompanionAgentUpdate(BaseModel):
    """Schema for updating a personal companion agent."""

    name: str | None = Field(None, min_length=1, max_length=255)
    avatar_url: str | None = Field(None, max_length=512)
    writing_style: str | None = Field(None, min_length=1)
    active: bool | None = None


class PersonalCompanionAgentRead(PersonalCompanionAgentBase):
    """Schema for reading a personal companion agent."""

    id: str
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
