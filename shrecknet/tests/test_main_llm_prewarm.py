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
        key = str(model)
        self.calls.append(key)
        if key in self.fail_on:
            raise RuntimeError(f"failed: {key}")
        return "ok"

    async def aclose(self) -> None:
        return None


def _settings(elder: str = "shared", librarian: str = "shared") -> SimpleNamespace:
    return SimpleNamespace(
        shreckllm_base_url="http://shreckllm:8110",
        shreckllm_request_timeout_s=60.0,
        llm_prewarm_on_startup=True,
        llm_prewarm_timeout_s=300.0,
        model_elder=LLMModelTarget(provider="ollama", name=elder),
        model_librarian=LLMModelTarget(provider="ollama", name=librarian),
        model_agents_repair_json=LLMModelTarget(provider="ollama", name=elder),
        model_architect_scene_chunking=LLMModelTarget(provider="ollama", name=elder),
        model_architect_entity_proposal=LLMModelTarget(provider="ollama", name=elder),
        model_architect_milestone_proposal=LLMModelTarget(provider="ollama", name=elder),
        model_architect_entity_generation=LLMModelTarget(provider="ollama", name=elder),
        model_novelist_planning=LLMModelTarget(provider="ollama", name=elder),
        model_novelist_prose=LLMModelTarget(provider="ollama", name=elder),
        model_novelist_critic=LLMModelTarget(provider="ollama", name=elder),
    )


async def _runtime(_settings):
    return {"operational_provider_ids": ["ollama"]}


@pytest.mark.asyncio
async def test_llm_prewarm_dedupes_identical_targets(monkeypatch):
    fake_settings = _settings()
    fake_client = _FakeClient()

    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)
    monkeypatch.setattr(main, "get_all_provider_validations", _runtime)

    await main._run_llm_prewarm()

    assert fake_client.calls == ["provider='ollama' name='shared'"]


@pytest.mark.asyncio
async def test_llm_prewarm_warms_each_unique_target(monkeypatch):
    fake_settings = _settings("elder-model", "librarian-model")
    fake_client = _FakeClient()

    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)
    monkeypatch.setattr(main, "get_all_provider_validations", _runtime)

    await main._run_llm_prewarm()

    assert fake_client.calls == [
        "provider='ollama' name='elder-model'",
        "provider='ollama' name='librarian-model'",
    ]


@pytest.mark.asyncio
async def test_llm_prewarm_includes_architect_and_novelist_targets(monkeypatch):
    fake_settings = _settings()
    fake_settings.model_architect_scene_chunking = LLMModelTarget(provider="ollama", name="architect-scenes")
    fake_settings.model_architect_entity_proposal = LLMModelTarget(provider="ollama", name="architect-proposals")
    fake_settings.model_novelist_planning = LLMModelTarget(provider="ollama", name="novelist-planning")
    fake_settings.model_novelist_prose = LLMModelTarget(provider="ollama", name="novelist-prose")
    fake_client = _FakeClient()
    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)
    monkeypatch.setattr(main, "get_all_provider_validations", _runtime)

    await main._run_llm_prewarm()

    assert "provider='ollama' name='architect-scenes'" in fake_client.calls
    assert "provider='ollama' name='architect-proposals'" in fake_client.calls
    assert "provider='ollama' name='novelist-planning'" in fake_client.calls
    assert "provider='ollama' name='novelist-prose'" in fake_client.calls


@pytest.mark.asyncio
async def test_llm_prewarm_failure_isolated_per_model(monkeypatch):
    fake_settings = _settings("elder-model", "librarian-model")
    fake_client = _FakeClient()
    fake_client.fail_on.add("provider='ollama' name='elder-model'")

    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)
    monkeypatch.setattr(main, "get_all_provider_validations", _runtime)

    await main._run_llm_prewarm()

    assert fake_client.calls == [
        "provider='ollama' name='elder-model'",
        "provider='ollama' name='librarian-model'",
    ]


@pytest.mark.asyncio
async def test_llm_prewarm_can_be_disabled(monkeypatch):
    fake_settings = _settings()
    fake_settings.llm_prewarm_on_startup = False
    fake_client = _FakeClient()
    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)
    monkeypatch.setattr(main, "get_all_provider_validations", _runtime)

    await main._run_llm_prewarm()

    assert fake_client.calls == []
