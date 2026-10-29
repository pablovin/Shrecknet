"""Typed, secret-free installation progress and verification contracts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class InstallationPublicStatus(BaseModel):
    enabled: bool
    owner_required: bool
    claim_mode: Literal["host_code", "local_browser"] = "host_code"

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


class InstallationServiceReadiness(BaseModel):
    id: Literal["shrecknet", "shreckrpg", "shreckllm", "shreckworld"]
    status: Literal["ready", "starting"]

class InstallationReadiness(BaseModel):
    services: list[InstallationServiceReadiness]


class InstallationAPI:
    """Administrator setup orchestration; provider secrets stay in ShreckLLM."""
    def __init__(self, client):
        self.client = client

    async def public_status(self) -> InstallationPublicStatus:
        return InstallationPublicStatus.model_validate(await self.client.raw_request("GET", "/setup/installation/status"))

    async def status(self) -> InstallationState:
        return InstallationState.model_validate(await self.client.raw_request("GET", "/setup/installation/state"))

    async def create_world(self, template: Literal["fantasy", "horror", "scifi"] = "fantasy", *, include_example: bool = True) -> InstallationState:
        payload = InstallationWorldRequest(template=template, include_example=include_example)
        return InstallationState.model_validate(await self.client.raw_request("POST", "/setup/installation/world", json=payload.model_dump()))

    async def configure_models(self, payload: InstallationModels) -> InstallationState:
        return InstallationState.model_validate(await self.client.raw_request("PUT", "/setup/installation/models", json=payload.model_dump()))

    async def complete_introduction(self) -> InstallationState:
        return InstallationState.model_validate(await self.client.raw_request("PUT", "/setup/installation/progress", json={"tour_completed": True}))

    async def verify(self, *, accept_provider_usage: bool) -> InstallationState:
        if accept_provider_usage is not True:
            raise ValueError("Provider usage consent is required")
        return InstallationState.model_validate(await self.client.raw_request("POST", "/setup/installation/verify", json={"accept_provider_usage": True}))

    async def complete(self) -> InstallationState:
        return InstallationState.model_validate(await self.client.raw_request("POST", "/setup/installation/complete", json={}))


    async def readiness(self) -> InstallationReadiness:
        return InstallationReadiness.model_validate(await self.client.raw_request("GET", "/setup/installation/readiness"))
