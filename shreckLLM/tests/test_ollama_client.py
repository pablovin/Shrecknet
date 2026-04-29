from __future__ import annotations

import httpx
import pytest

from app.errors import DependencyUnavailableError, InvalidModelError, ProviderOverloadedError
from app.ollama_client import OllamaClient
from app.schemas import ChatMessage


@pytest.mark.asyncio
async def test_ollama_chat_parses_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            return httpx.Response(
                200,
                json={
                    "message": {"content": "pong"},
                    "prompt_eval_count": 10,
                    "eval_count": 3,
                },
            )
        return httpx.Response(404)

    client = OllamaClient(base_url="http://test", timeout_s=10)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")

    out = await client.chat(
        model="m",
        messages=[ChatMessage(role="user", content="ping")],
        temperature=0.7,
        max_tokens=100,
    )
    assert out["text"] == "pong"
    assert out["usage"]["total_tokens"] == 13


@pytest.mark.asyncio
async def test_ollama_maps_404_to_invalid_model() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404, json={"error": "not found"})

    client = OllamaClient(base_url="http://test", timeout_s=10)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")

    with pytest.raises(InvalidModelError):
        await client.list_models()


@pytest.mark.asyncio
async def test_ollama_maps_503_to_overloaded() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503, json={"error": "busy"})

    client = OllamaClient(base_url="http://test", timeout_s=10)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")

    with pytest.raises(ProviderOverloadedError):
        await client.list_models()


@pytest.mark.asyncio
async def test_ollama_connection_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = OllamaClient(base_url="http://test", timeout_s=10)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")

    with pytest.raises(DependencyUnavailableError):
        await client.list_models()
