from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(min_length=1)
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    provider_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    conversation_id: str | None = None
    use_conversation_memory: bool = False
    metadata: dict[str, Any] | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=131072)


class ChatUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ChatResponse(BaseModel):
    text: str
    provider_id: str
    requested_model: str | None = None
    resolved_model: str
    provider_request_id: str | None = None
    # Backward-friendly mirror for consumers that still read `model`.
    model: str
    usage: ChatUsage
    latency_ms: float
    conversation_id: str | None = None
    memory_applied: bool
    metadata: dict[str, Any] | None = None


class ChatJobCreateResponse(BaseModel):
    job_id: str
    status: str
    created_at: float
    expires_at: float | None = None


class ChatJobStatusResponse(BaseModel):
    job_id: str
    status: str
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    expires_at: float | None = None
    provider_id: str | None = None
    resolved_model: str | None = None
    requested_model: str | None = None
    retry_count: int = 0
    error: str | None = None


class ServiceStatusResponse(BaseModel):
    service: str = "shreckLLM"
    shreckllm_operational: bool = False
    operational_provider_ids: list[str] = Field(default_factory=list)
    providers_summary: dict[str, Any] = Field(default_factory=dict)
    memory_backend: str = "redis"
    redis_url: str
    in_flight_requests: int
    waiting_requests: int
    max_concurrent_requests: int
    request_timeout_seconds: float
    max_queue_wait_seconds: float
    dependencies: dict[str, Any]
    provider_limiters: dict[str, Any] | None = None


class OpenAITokenUpdateRequest(BaseModel):
    api_key: str = Field(min_length=1)


class OpenAIValidationResponse(BaseModel):
    configured: bool
    present: bool
    valid: bool | None
    error: str | None = None


class AnthropicTokenUpdateRequest(BaseModel):
    api_key: str = Field(min_length=1)


class AnthropicValidationResponse(BaseModel):
    configured: bool
    present: bool
    valid: bool | None
    error: str | None = None
