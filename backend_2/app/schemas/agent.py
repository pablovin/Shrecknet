"""Pydantic schemas for Agent model."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentBase(BaseModel):
    """Base schema for Agent."""
    
    name: str = Field(..., min_length=1, max_length=255, description="Agent name")
    avatar_url: Optional[str] = Field(None, max_length=512, description="Avatar URL")
    description: Optional[str] = Field(None, description="Agent description")
    writing_style: Optional[str] = Field(None, description="Agent writing style/persona")
    job: str = Field("elder", max_length=50, description="Job type (e.g., 'elder')")
    active: bool = Field(True, description="Whether the agent is active")


class AgentCreate(AgentBase):
    """Schema for creating a new Agent."""
    
    ontology_ids: list[int] = Field(default_factory=list, description="List of ontology IDs to link")


class AgentUpdate(BaseModel):
    """Schema for updating an Agent."""
    
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    avatar_url: Optional[str] = Field(None, max_length=512)
    description: Optional[str] = None
    writing_style: Optional[str] = None
    job: Optional[str] = Field(None, max_length=50)
    active: Optional[bool] = None


class AgentRead(AgentBase):
    """Schema for reading an Agent."""
    
    id: str = Field(..., description="Agent UUID")
    created_at: datetime
    updated_at: datetime
    ontology_ids: list[int] = Field(default_factory=list, description="Linked ontology IDs")
    
    model_config = ConfigDict(from_attributes=True)
