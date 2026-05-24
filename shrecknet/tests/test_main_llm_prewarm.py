from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import main
from app.core.config_store import LLMModelTarget


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, D401
        del args, kwargs
        self.calls: list[tuple[str, str]] = []
        self.fail_on: set[tuple[str, str]] = set()

    async def chat(self, *, model, messages, temperature, usage_tag):  # noqa: ANN001
        del messages, temperature, usage_tag
        key = (model.provider, model.name)
        self.calls.append(key)
        if key in self.fail_on:
            raise RuntimeError(f"failed: {key[0]}:{key[1]}")
        return "ok"

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_llm_prewarm_dedupes_identical_targets(monkeypatch):
    shared = LLMModelTarget(provider="ollama", name="gemma4:e4b")
    fake_settings = SimpleNamespace(
        model_architect_scene_chunking=shared,
        model_architect=shared,
        model_elder=shared,
        model_librarian=shared,
        model_novelist=shared,
        model_novelist_draft=shared,
        shreckllm_base_url="http://shreckllm:8110",
        shreckllm_request_timeout_s=60.0,
    )
    fake_client = _FakeClient()

    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)

    await main._run_llm_prewarm()

    assert fake_client.calls == [("ollama", "gemma4:e4b")]


@pytest.mark.asyncio
async def test_llm_prewarm_warms_each_unique_target(monkeypatch):
    fake_settings = SimpleNamespace(
        model_architect_scene_chunking=LLMModelTarget(provider="ollama", name="gemma4:e4b"),
        model_architect=LLMModelTarget(provider="openai", name="gpt-5-nano"),
        model_elder=LLMModelTarget(provider="openai", name="gpt-5-nano"),
        model_librarian=LLMModelTarget(provider="openai", name="gpt-4o-mini"),
        model_novelist=LLMModelTarget(provider="ollama", name="qwen2.5:7b"),
        model_novelist_draft=LLMModelTarget(provider="ollama", name="gemma4:e4b"),
        shreckllm_base_url="http://shreckllm:8110",
        shreckllm_request_timeout_s=60.0,
    )
    fake_client = _FakeClient()

    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)

    await main._run_llm_prewarm()

    assert fake_client.calls == [
        ("ollama", "gemma4:e4b"),
        ("openai", "gpt-5-nano"),
        ("openai", "gpt-4o-mini"),
        ("ollama", "qwen2.5:7b"),
    ]


@pytest.mark.asyncio
async def test_llm_prewarm_failure_isolated_per_model(monkeypatch):
    fake_settings = SimpleNamespace(
        model_architect_scene_chunking=LLMModelTarget(provider="ollama", name="gemma4:e4b"),
        model_architect=LLMModelTarget(provider="openai", name="gpt-5-nano"),
        model_elder=LLMModelTarget(provider="openai", name="gpt-5-nano"),
        model_librarian=LLMModelTarget(provider="openai", name="gpt-4o-mini"),
        model_novelist=LLMModelTarget(provider="ollama", name="qwen2.5:7b"),
        model_novelist_draft=LLMModelTarget(provider="ollama", name="gemma4:e4b"),
        shreckllm_base_url="http://shreckllm:8110",
        shreckllm_request_timeout_s=60.0,
    )
    fake_client = _FakeClient()
    fake_client.fail_on.add(("openai", "gpt-5-nano"))

    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)

    await main._run_llm_prewarm()

    assert fake_client.calls == [
        ("ollama", "gemma4:e4b"),
        ("openai", "gpt-5-nano"),
        ("openai", "gpt-4o-mini"),
        ("ollama", "qwen2.5:7b"),
    ]

