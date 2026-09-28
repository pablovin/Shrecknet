from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FoundryIntegrationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    allowed_world_ids: list[str] = Field(default_factory=list, max_length=200)


class FoundryIntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    key_prefix: str
    allowed_world_ids: list[str] = Field(default_factory=list)
    active: bool
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class FoundryIntegrationCreated(FoundryIntegrationRead):
    """The secret is returned exactly once, at creation time."""

    key: str


class FoundryIntegrationValidation(BaseModel):
    valid: bool = True
    server_name: str = "Shrecknet"
    version: str


class FoundryWorldRead(BaseModel):
    id: str
    name: str
