from __future__ import annotations

from pydantic import BaseModel, Field


class FoundryIntegrationConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    allowed_world_ids: list[str] | None = Field(default=None, max_length=200)


class FoundryIntegrationConfigSummary(BaseModel):
    id: str = "foundry_integrations"
    label: str = "Foundry integrations"
    collection_url: str = "/config/integrations/foundry"
