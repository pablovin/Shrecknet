from __future__ import annotations

import pytest

from app.integrations.llm.shreckllm_client import ShreckLLMClient


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _HTTP:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.gets: list[str] = []

    async def get(self, path: str, **kwargs):
        del kwargs
        self.gets.append(path)
        return _Response(self.payload)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_provider_preflight_dedupes_success() -> None:
    client = ShreckLLMClient(base_url="http://test")
    fake_http = _HTTP({"providers": {"openai": {"active": True}}})
    client._http = fake_http  # type: ignore[assignment]

    await client.ensure_provider_ready("openai")
    await client.ensure_provider_ready("openai")

    assert fake_http.gets == ["/providers"]
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_preflight_failure_raises_clear_message() -> None:
    client = ShreckLLMClient(base_url="http://test")
    client._http = _HTTP({"providers": {"openai": {
        "active": False, "last_validation_error": "missing_api_key",
    }}})  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="LLM provider openai failed validation: missing_api_key"):
        await client.ensure_provider_ready("openai")

    await client.aclose()


@pytest.mark.asyncio
async def test_elder_call_logs_preflight_header_and_records_wait(capsys, monkeypatch) -> None:
    client = ShreckLLMClient(base_url="http://test")

    async def ready(_provider_id: str) -> None:
        return None

    async def submit(_payload: dict) -> str:
        return "job-1"

    async def wait(_job_id: str, **_kwargs) -> dict:
        return {
            "text": "answer",
            "provider_id": "deepinfra",
            "resolved_model": "Qwen/Qwen3",
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        }

    monkeypatch.setattr(client, "ensure_provider_ready", ready)
    monkeypatch.setattr(client, "submit_chat_job", submit)
    monkeypatch.setattr(client, "wait_for_chat_job", wait)

    answer = await client.chat(
        model={"provider": "deepinfra", "name": "Qwen/Qwen3"},
        messages=[{"role": "user", "content": "A substantial Elder question"}],
        usage_tag="elder.v2.trace-1.synthesize",
    )

    assert answer == "answer"
    console = capsys.readouterr().out
    assert "[ELDER_LLM_REQUEST] stage=synthesize" in console
    assert "input_tokens_est=" in console
    assert "[ELDER_LLM_RESPONSE] stage=synthesize" in console
    event = client.get_usage_events_since(0)[0]
    assert event["wait_ms"] >= 0
    await client.aclose()


@pytest.mark.asyncio
async def test_poll_timeout_never_submits_duplicate_chat_job(monkeypatch) -> None:
    client = ShreckLLMClient(base_url="http://test", max_retries=3)
    submissions = 0

    async def ready(_provider_id: str) -> None:
        return None

    async def submit(_payload: dict) -> str:
        nonlocal submissions
        submissions += 1
        return "job-1"

    async def timeout(_job_id: str, **_kwargs) -> dict:
        import httpx

        raise httpx.TimeoutException("polling deadline")

    monkeypatch.setattr(client, "ensure_provider_ready", ready)
    monkeypatch.setattr(client, "submit_chat_job", submit)
    monkeypatch.setattr(client, "wait_for_chat_job", timeout)

    with pytest.raises(Exception, match="polling deadline"):
        await client.chat(
            model={"provider": "deepinfra", "name": "Qwen/Qwen3"},
            messages=[{"role": "user", "content": "hello"}],
        )

    assert submissions == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_polling_has_no_caller_generation_deadline(monkeypatch) -> None:
    client = ShreckLLMClient(
        base_url="http://test",
        timeout=0.1,
        poll_without_deadline=False,
    )
    observed_timeout = object()

    async def ready(_provider_id: str) -> None:
        return None

    async def submit(_payload: dict) -> str:
        return "job-1"

    async def wait(_job_id: str, *, timeout_s, **_kwargs) -> dict:
        nonlocal observed_timeout
        observed_timeout = timeout_s
        return {
            "text": "answer",
            "provider_id": "deepinfra",
            "resolved_model": "Qwen/Qwen3",
            "usage": {},
        }

    monkeypatch.setattr(client, "ensure_provider_ready", ready)
    monkeypatch.setattr(client, "submit_chat_job", submit)
    monkeypatch.setattr(client, "wait_for_chat_job", wait)

    answer = await client.chat(
        model={"provider": "deepinfra", "name": "Qwen/Qwen3"},
        messages=[{"role": "user", "content": "hello"}],
    )

    assert answer == "answer"
    assert observed_timeout is None
    await client.aclose()
