from datetime import datetime, timezone

import pytest

from app import openai_client
from app.config_store import ProviderDefaults
from app.schemas import ChatMessage
from app.service import ChatService


def test_openai_compatible_client_disables_sdk_retries(monkeypatch) -> None:
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openai_client, "AsyncOpenAI", FakeAsyncOpenAI)

    client = openai_client.OpenAIClient(
        api_key="secret",
        timeout_s=45,
        base_url="https://example.test/v1",
        provider_id="deepinfra",
    )

    assert client.configured is True
    assert captured["max_retries"] == 0


@pytest.mark.parametrize(
    ("provider_id", "base_url"),
    [
        ("deepinfra", "https://api.deepinfra.com/v1/openai"),
        ("openrouter", "https://openrouter.ai/api/v1"),
    ],
)
def test_openai_compatible_providers_use_authoritative_runtime_timeout(
    monkeypatch, provider_id, base_url
) -> None:
    captured = {}

    class FakeOpenAIClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("app.service.OpenAIClient", FakeOpenAIClient)
    service = object.__new__(ChatService)
    service._runtime = type("Runtime", (), {"request_timeout_seconds": 45.0})()

    service._build_provider_adapter(
        provider_id,
        ProviderDefaults(api_key="secret", base_url=base_url),
    )

    assert captured["provider_id"] == provider_id
    assert captured["base_url"] == base_url
    assert captured["timeout_s"] == 45.0


@pytest.mark.asyncio
async def test_deepinfra_chat_explicitly_uses_default_service_tier(monkeypatch) -> None:
    captured = {}

    class Response:
        choices = []
        usage = None
        id = "request-1"

    class Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return Response()

    class Chat:
        completions = Completions()

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = Chat()

    monkeypatch.setattr(openai_client, "AsyncOpenAI", FakeAsyncOpenAI)
    client = openai_client.OpenAIClient(
        api_key="secret",
        timeout_s=45,
        base_url="https://api.deepinfra.com/v1/openai",
        provider_id="deepinfra",
    )

    await client.chat(
        model="Qwen/Qwen3",
        messages=[ChatMessage(role="user", content="hello")],
        temperature=0.3,
    )

    assert captured["extra_body"] == {"service_tier": "default"}


@pytest.mark.asyncio
async def test_openai_compatible_chat_forwards_strict_response_format(monkeypatch) -> None:
    captured = {}

    class Response:
        choices = []
        usage = None
        id = "request-1"

    class Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return Response()

    class Chat:
        completions = Completions()

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = Chat()

    monkeypatch.setattr(openai_client, "AsyncOpenAI", FakeAsyncOpenAI)
    client = openai_client.OpenAIClient(api_key="secret", timeout_s=45)
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "plan", "strict": True, "schema": {"type": "object"}},
    }

    await client.chat(
        model="gpt-5-nano",
        messages=[ChatMessage(role="user", content="plan")],
        temperature=0.1,
        response_format=response_format,
    )

    assert captured["response_format"] == response_format


@pytest.mark.asyncio
async def test_openai_chat_does_not_send_deepinfra_service_tier(monkeypatch) -> None:
    captured = {}

    class Response:
        choices = []
        usage = None
        id = "request-1"

    class Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return Response()

    class Chat:
        completions = Completions()

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = Chat()

    monkeypatch.setattr(openai_client, "AsyncOpenAI", FakeAsyncOpenAI)
    client = openai_client.OpenAIClient(
        api_key="secret",
        timeout_s=45,
        provider_id="openai",
    )

    await client.chat(
        model="gpt-4o-mini",
        messages=[ChatMessage(role="user", content="hello")],
        temperature=0.3,
    )

    assert "extra_body" not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selected_model", "execution_model"),
    [
        ("anthropic/claude-sonnet-4", "anthropic/claude-sonnet-4:nitro"),
        ("anthropic/claude-sonnet-4:nitro", "anthropic/claude-sonnet-4:nitro"),
        ("anthropic/claude-sonnet-4:NITRO", "anthropic/claude-sonnet-4:nitro"),
    ],
)
async def test_openrouter_chat_uses_nitro_routing_and_native_reasoning_flag(
    monkeypatch, selected_model, execution_model
) -> None:
    captured = {}

    class Response:
        choices = []
        usage = None
        id = "request-1"

    class Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return Response()

    class Chat:
        completions = Completions()

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = Chat()

    monkeypatch.setattr(openai_client, "AsyncOpenAI", FakeAsyncOpenAI)
    client = openai_client.OpenAIClient(
        api_key="secret",
        timeout_s=300,
        base_url="https://openrouter.ai/api/v1",
        provider_id="openrouter",
    )

    await client.chat(
        model=selected_model,
        messages=[ChatMessage(role="user", content="hello")],
        temperature=0.3,
        reasoning=True,
    )

    assert captured["model"] == execution_model
    assert captured["extra_body"] == {
        "reasoning": {"enabled": True, "effort": "high"}
    }


@pytest.mark.asyncio
async def test_openrouter_chat_disables_reasoning_by_default(monkeypatch) -> None:
    captured = {}

    class Response:
        choices = []
        usage = None
        id = "request-1"

    class Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return Response()

    class Chat:
        completions = Completions()

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = Chat()

    monkeypatch.setattr(openai_client, "AsyncOpenAI", FakeAsyncOpenAI)
    client = openai_client.OpenAIClient(
        api_key="secret",
        timeout_s=300,
        base_url="https://openrouter.ai/api/v1",
        provider_id="openrouter",
    )

    await client.chat(
        model="anthropic/claude-sonnet-4",
        messages=[ChatMessage(role="user", content="hello")],
        temperature=0.3,
    )

    assert captured["extra_body"] == {"reasoning": {"enabled": False}}


def test_retry_after_seconds_supports_seconds_and_http_dates() -> None:
    class Response:
        headers = {"retry-after": "12.5"}

    exc = RuntimeError()
    exc.response = Response()
    assert openai_client._retry_after_seconds(exc) == 12.5

    Response.headers = {"retry-after": "Wed, 21 Oct 2015 07:28:10 GMT"}
    assert openai_client._retry_after_seconds(
        exc,
        now=datetime(2015, 10, 21, 7, 28, 0, tzinfo=timezone.utc),
    ) == 10.0
