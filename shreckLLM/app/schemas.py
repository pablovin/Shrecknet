from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(min_length=1)
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    provider_id: str = Field(min_length=1)
    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    conversation_id: str | None = None
    use_conversation_memory: bool = False
    metadata: dict[str, Any] | None = None


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


class ServiceStatusResponse(BaseModel):
    service: str = "shreckLLM"
    default_provider_id: str
    memory_backend: str = "redis"
    redis_url: str
    in_flight_requests: int
    waiting_requests: int
    max_concurrent_requests: int
    request_timeout_seconds: float
    max_queue_wait_seconds: float
    dependencies: dict[str, Any]


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
