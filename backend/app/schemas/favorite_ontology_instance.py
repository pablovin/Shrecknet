from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FavoriteOntologyInstanceCreate(BaseModel):
    """Schema for creating a favorite."""

    model_config = ConfigDict(extra="ignore")
    ontology_id: int


class FavoriteOntologyInstanceRead(BaseModel):
    """Schema for reading a favorite."""

    model_config = ConfigDict(extra="ignore")
    id: int
    user_id: int
    instance_id: str
    ontology_id: int
    created_at: datetime


class FavoriteStatusRead(BaseModel):
    """Schema for checking if an instance is favorited."""

    model_config = ConfigDict(extra="ignore")
    is_favorite: bool
