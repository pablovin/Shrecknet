from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import main


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, D401
        del args, kwargs
        self.calls: list[tuple[str, str]] = []
        self.fail_on: set[tuple[str, str]] = set()

    async def chat(self, *, model, messages, temperature, usage_tag):  # noqa: ANN001
        del messages, temperature, usage_tag
        key = str(model)
        self.calls.append(key)
        if key in self.fail_on:
            raise RuntimeError(f"failed: {key}")
        return "ok"

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_llm_prewarm_dedupes_identical_targets(monkeypatch):
    fake_settings = SimpleNamespace(
        shreckllm_base_url="http://shreckllm:8110",
        shreckllm_request_timeout_s=60.0,
    )
    fake_client = _FakeClient()

    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)

    await main._run_llm_prewarm()

    assert fake_client.calls == ["gpt-5-nano"]


@pytest.mark.asyncio
async def test_llm_prewarm_warms_each_unique_target(monkeypatch):
    fake_settings = SimpleNamespace(
        shreckllm_base_url="http://shreckllm:8110",
        shreckllm_request_timeout_s=60.0,
    )
    fake_client = _FakeClient()

    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)

    await main._run_llm_prewarm()

    assert fake_client.calls == ["gpt-5-nano"]


@pytest.mark.asyncio
async def test_llm_prewarm_failure_isolated_per_model(monkeypatch):
    fake_settings = SimpleNamespace(
        shreckllm_base_url="http://shreckllm:8110",
        shreckllm_request_timeout_s=60.0,
    )
    fake_client = _FakeClient()
    fake_client.fail_on.add("gpt-5-nano")

    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)

    await main._run_llm_prewarm()

    assert fake_client.calls == ["gpt-5-nano"]
