from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import main
from app.core.config_store import LLMModelTarget

_DEFAULT_TARGET = LLMModelTarget(provider="openai", name="gpt-5-nano")
_DEFAULT_TARGET_GPT5 = LLMModelTarget(provider="openai", name="gpt-5")

_FAKE_SETTINGS_BASE = dict(
    shreckllm_base_url="http://shreckllm:8110",
    shreckllm_request_timeout_s=60.0,
    model_architect_scene_chunking=_DEFAULT_TARGET,
    model_architect_entity_proposal=_DEFAULT_TARGET,
    model_architect_milestone_proposal=_DEFAULT_TARGET,
    model_architect_entity_generation=_DEFAULT_TARGET,
    model_agents_repair_json=_DEFAULT_TARGET,
    model_elder=_DEFAULT_TARGET,
    model_librarian=_DEFAULT_TARGET,
    model_novelist_planning=_DEFAULT_TARGET,
    model_novelist_prose=_DEFAULT_TARGET,
    model_novelist_critic=_DEFAULT_TARGET,
)


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, D401
        del args, kwargs
        self.calls: list[tuple[str, str]] = []
        self.fail_on: set[tuple[str, str]] = set()
        self.provider_auth: dict[str, bool] = {}

    async def fetch_provider_auth_statuses(self) -> dict[str, bool]:
        return self.provider_auth

    async def chat(self, *, model, messages, temperature, usage_tag):  # noqa: ANN001
        del messages, temperature, usage_tag
        if hasattr(model, "provider") and hasattr(model, "name"):
            key = f"{model.provider}:{model.name}"
        else:
            key = str(model)
        self.calls.append(key)
        if key in self.fail_on:
            raise RuntimeError(f"failed: {key}")
        return "ok"

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_llm_prewarm_dedupes_identical_targets(monkeypatch):
    fake_settings = SimpleNamespace(**_FAKE_SETTINGS_BASE)
    fake_client = _FakeClient()

    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)

    await main._run_llm_prewarm()

    assert fake_client.calls == ["openai:gpt-5-nano"]


@pytest.mark.asyncio
async def test_llm_prewarm_warms_each_unique_target(monkeypatch):
    settings_dict = dict(_FAKE_SETTINGS_BASE)
    settings_dict["model_novelist_prose"] = _DEFAULT_TARGET_GPT5
    fake_settings = SimpleNamespace(**settings_dict)
    fake_client = _FakeClient()

    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)

    await main._run_llm_prewarm()

    assert sorted(fake_client.calls) == ["openai:gpt-5", "openai:gpt-5-nano"]


@pytest.mark.asyncio
async def test_llm_prewarm_failure_isolated_per_model(monkeypatch):
    fake_settings = SimpleNamespace(**_FAKE_SETTINGS_BASE)
    fake_client = _FakeClient()
    fake_client.fail_on.add("openai:gpt-5-nano")

    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)

    await main._run_llm_prewarm()

    assert fake_client.calls == ["openai:gpt-5-nano"]


@pytest.mark.asyncio
async def test_llm_prewarm_skips_unconfigured_provider(monkeypatch):
    """Prewarm must be skipped entirely when the provider reports auth not configured."""
    fake_settings = SimpleNamespace(**_FAKE_SETTINGS_BASE)
    fake_client = _FakeClient()
    fake_client.provider_auth = {"openai": False}

    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)

    await main._run_llm_prewarm()

    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_llm_prewarm_proceeds_when_auth_status_unavailable(monkeypatch):
    """If auth-status fetch returns empty (network error), prewarm proceeds normally."""
    fake_settings = SimpleNamespace(**_FAKE_SETTINGS_BASE)
    fake_client = _FakeClient()
    # provider_auth defaults to {} → treated as "unknown, proceed"

    monkeypatch.setattr(main, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(main, "ShreckLLMClient", lambda **kwargs: fake_client)

    await main._run_llm_prewarm()

    assert fake_client.calls == ["openai:gpt-5-nano"]

