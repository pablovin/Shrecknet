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
        self.posts: list[str] = []

    async def post(self, path: str, **kwargs):
        del kwargs
        self.posts.append(path)
        return _Response(self.payload)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_provider_preflight_dedupes_success() -> None:
    client = ShreckLLMClient(base_url="http://test")
    fake_http = _HTTP({"provider": {"active": True}})
    client._http = fake_http  # type: ignore[assignment]

    await client.ensure_provider_ready("openai")
    await client.ensure_provider_ready("openai")

    assert fake_http.posts == ["/providers/openai/test"]
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_preflight_failure_raises_clear_message() -> None:
    client = ShreckLLMClient(base_url="http://test")
    client._http = _HTTP({"provider": {"active": False, "last_validation_error": "missing_api_key"}})  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="LLM provider openai failed validation: missing_api_key"):
        await client.ensure_provider_ready("openai")

    await client.aclose()
