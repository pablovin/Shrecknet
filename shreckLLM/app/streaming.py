"""Incremental provider transport. No retries/hedges after an exposed token.

The gateway shares existing global/provider concurrency controls. Only inline,
validated image data is accepted; providers never fetch user-supplied URLs.
"""
from __future__ import annotations
import asyncio
import base64
import binascii
import json
import time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.schemas import ReasoningEffort
from app.errors import InvalidModelError, ProviderBadRequestError


class ContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text", "image_url"]
    text: str | None = Field(None, max_length=100000)
    image_url: dict[str, str] | None = None

    @field_validator("image_url")
    @classmethod
    def inline_image(cls, value):
        if value is not None:
            url = value.get("url", "")
            if not url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")) or len(url) > 15_000_000:
                raise ValueError("Only bounded inline PNG/JPEG/WebP images accepted")
            try:
                base64.b64decode(url.split(",", 1)[1], validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("Invalid inline image") from exc
        return value

    @model_validator(mode="after")
    def required_content(self):
        if self.type == "text" and (self.text is None or self.image_url is not None):
            raise ValueError("Text part requires only text")
        if self.type == "image_url" and (self.image_url is None or self.text is not None):
            raise ValueError("Image part requires only image_url")
        return self


class StreamMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: str | list[ContentPart]


class StreamChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    messages: list[StreamMessage] = Field(min_length=1, max_length=100)
    reasoning: bool = False
    web_search_enabled: bool = False
    reasoning_effort: ReasoningEffort | None = None
    temperature: float = Field(0.7, ge=0, le=2)
    max_tokens: int = Field(2000, ge=1, le=16000)
    metadata: dict | None = None


def provider_messages(request, provider):
    rows = [m.model_dump(exclude_none=True) for m in request.messages]
    if provider == "anthropic":
        system = "\n".join(m["content"] for m in rows if m["role"] == "system" and isinstance(m["content"], str))
        rows = [m for m in rows if m["role"] != "system"]
        for row in rows:
            if isinstance(row["content"], list):
                row["content"] = [({"type": "image", "source": {"type": "base64", "media_type": p["image_url"]["url"].split(";")[0][5:], "data": p["image_url"]["url"].split(",", 1)[1]}} if p["type"] == "image_url" else p) for p in row["content"]]
        return rows, system
    if provider in {"ollama", "ollama_cloud"}:
        for row in rows:
            if isinstance(row["content"], list):
                row["images"] = [p["image_url"]["url"].split(",", 1)[1] for p in row["content"] if p["type"] == "image_url"]
                row["content"] = "\n".join(p["text"] for p in row["content"] if p["type"] == "text")
    return rows, ""


async def stream_chat(service, request):
    adapter = service.registry.get(request.provider_id)
    if adapter is None or request.provider_id not in service._runtime.provider_defaults:
        raise InvalidModelError("Streaming provider is not configured")
    from app.web_search import validate_web_search
    await validate_web_search(adapter, request.model, request.web_search_enabled)
    sem = service._provider_semaphores.get(request.provider_id)
    async with service.limiter.slot(wait_timeout_s=service._runtime.max_queue_wait_seconds):
        acquired = False
        try:
            if sem is not None:
                await asyncio.wait_for(sem.acquire(), service._runtime.max_queue_wait_seconds)
                acquired = True
            await service._wait_for_provider_cooldown(request.provider_id)
            # One provider attempt for the entire stream. Timeout also bounds a peer
            # that keeps the socket alive without completing a response.
            iterator = adapter.stream(request)
            deadline = time.monotonic() + service._runtime.request_timeout_seconds
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(iterator.__anext__(), max(0.01, deadline - time.monotonic()))
                    except StopAsyncIteration:
                        break
                    yield event
            finally:
                await iterator.aclose()
        finally:
            if acquired:
                sem.release()
