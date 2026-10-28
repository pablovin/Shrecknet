"""Typed, secret-free installation progress and verification contracts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class InstallationPublicStatus(BaseModel):
    enabled: bool
    owner_required: bool

class InstallationState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    world_id: int | None = None
    world_name: str = ""
    world_template: str | None = None
    example_page_id: str | None = None
    example_scene_id: str | None = None
    agents_configured: bool = False
    model_fingerprint: str | None = None
    agent_ids: dict[str, str] = Field(default_factory=dict)
    tour_completed: bool = False
    completed: bool = False
    verification_id: str | None = None
    verification_status: Literal["not_started", "queued", "running", "passed", "failed"] = "not_started"
    verification_code: str | None = None
    verification_started_at: float | None = None
    verified_targets: list[str] = Field(default_factory=list)

class InstallationProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tour_completed: bool

class InstallationWorldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: Literal["fantasy", "horror", "scifi"] = "fantasy"
    include_example: bool = True

class InstallationModels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    model: str = Field(min_length=1, max_length=255)
    jobs: list[Literal["elder", "architect", "novelist", "librarian", "character_agent", "companion"]] = Field(min_length=1, max_length=6)

class InstallationVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accept_provider_usage: Literal[True]
