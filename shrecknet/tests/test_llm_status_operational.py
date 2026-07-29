from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.routers import llm_status


def _settings() -> SimpleNamespace:
    target = SimpleNamespace(provider="openai", name="gpt-5-nano")
    return SimpleNamespace(
        model_architect_scene_chunking=target,
        model_architect_entity_proposal=target,
        model_architect_milestone_proposal=target,
        model_architect_entity_generation=target,
        model_agents_repair_json=target,
        model_elder_planner=target,
        model_elder_synthesis=target,
        model_novelist_planning=target,
        model_novelist_prose=target,
        model_novelist_critic=target,
        model_librarian_planner=target,
        model_librarian_synthesis=target,
    )


@pytest.mark.asyncio
async def test_llm_status_reachable_but_not_operational(monkeypatch) -> None:
    async def fake_shreckllm_status(_settings):
        return {"configured": True, "reachable": True, "operational": False, "error": None}

    async def fake_provider_validations(_settings):
        return {
            "shreckllm_operational": False,
            "operational_provider_ids": [],
            "providers": {
                "openai": {
                    "active": False,
                    "models": [{"model": "gpt-5-nano", "available": False}],
                }
            },
            "error": None,
        }

    monkeypatch.setattr(llm_status, "get_settings", _settings)
    monkeypatch.setattr(llm_status, "get_shreckllm_status", fake_shreckllm_status)
    monkeypatch.setattr(llm_status, "get_all_provider_validations", fake_provider_validations)

    payload = await llm_status.get_service_status(_current_user=object())

    assert payload["shreckllm_operational"] is False
    assert payload["shreckllm"]["reachable"] is True
    assert payload["shreckllm"]["operational"] is False
    assert payload["services"]["architect"] is False


@pytest.mark.asyncio
async def test_llm_status_operational_when_provider_valid(monkeypatch) -> None:
    async def fake_shreckllm_status(_settings):
        return {"configured": True, "reachable": True, "operational": False, "error": None}

    async def fake_provider_validations(_settings):
        return {
            "shreckllm_operational": True,
            "operational_provider_ids": ["openai"],
            "providers": {
                "openai": {
                    "active": True,
                    "models": [{"model": "gpt-5-nano", "available": True}],
                }
            },
            "error": None,
        }

    monkeypatch.setattr(llm_status, "get_settings", _settings)
    monkeypatch.setattr(llm_status, "get_shreckllm_status", fake_shreckllm_status)
    monkeypatch.setattr(llm_status, "get_all_provider_validations", fake_provider_validations)

    payload = await llm_status.get_service_status(_current_user=object())

    assert payload["shreckllm_operational"] is True
    assert payload["shreckllm"]["operational"] is True
    assert payload["shreckllm"]["operational_provider_ids"] == ["openai"]
    assert payload["services"]["architect"] is True


@pytest.mark.asyncio
async def test_llm_status_models_proxy_returns_catalog(monkeypatch) -> None:
    async def fake_provider_model_catalog(_settings):
        return {
            "providers": {
                "openai": {
                    "configured_models": ["gpt-5-nano"],
                    "discovered_models": ["gpt-5-nano", "gpt-5"],
                    "models": ["gpt-5-nano", "gpt-5"],
                }
            },
            "error": None,
        }

    monkeypatch.setattr(llm_status, "get_settings", _settings)
    monkeypatch.setattr(llm_status, "get_provider_model_catalog", fake_provider_model_catalog)

    payload = await llm_status.get_service_model_catalog(_current_user=object())

    assert payload["error"] is None
    assert payload["providers"]["openai"]["discovered_models"] == ["gpt-5-nano", "gpt-5"]
