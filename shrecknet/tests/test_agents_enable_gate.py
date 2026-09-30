from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import agent_feature_gate
from app.api.routers import architect, configurations
from app.core.config_store import Settings


@pytest.mark.asyncio
async def test_enable_agents_requires_operational_shreckllm(monkeypatch) -> None:
    persisted_updates: list[dict] = []

    def fake_get_settings():
        return Settings()

    async def fake_provider_validations(_settings):
        return {
            "shreckllm_operational": False,
            "operational_provider_ids": [],
            "providers": {},
            "error": "validation_unavailable",
        }

    def fake_update_settings(updates):
        persisted_updates.append(updates)
        return SimpleNamespace(model_dump=lambda: {"enable_ai_agents": True})

    monkeypatch.setattr(configurations, "get_settings", fake_get_settings)
    monkeypatch.setattr(agent_feature_gate, "get_settings", fake_get_settings)
    monkeypatch.setattr(agent_feature_gate, "get_all_provider_validations", fake_provider_validations)
    monkeypatch.setattr(configurations, "get_all_provider_validations", fake_provider_validations)
    monkeypatch.setattr(configurations, "update_settings", fake_update_settings)

    with pytest.raises(HTTPException) as exc_info:
        await configurations._put_config_payload({"enable_ai_agents": True})

    assert exc_info.value.status_code == 503
    assert "shreckLLM is operational" in str(exc_info.value.detail)
    assert persisted_updates == []


@pytest.mark.asyncio
async def test_enable_agents_persists_when_shreckllm_operational(monkeypatch) -> None:
    persisted_updates: list[dict] = []
    active_settings = Settings()

    def fake_get_settings():
        return active_settings

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

    def fake_update_settings(updates):
        nonlocal active_settings
        persisted_updates.append(updates)
        active_settings = active_settings.model_copy(update=updates)
        return active_settings

    monkeypatch.setattr(configurations, "get_settings", fake_get_settings)
    monkeypatch.setattr(agent_feature_gate, "get_settings", fake_get_settings)
    monkeypatch.setattr(agent_feature_gate, "get_all_provider_validations", fake_provider_validations)
    monkeypatch.setattr(configurations, "get_all_provider_validations", fake_provider_validations)
    monkeypatch.setattr(configurations, "update_settings", fake_update_settings)
    monkeypatch.setattr(configurations, "configure_celery_app", lambda: None)

    payload = await configurations._put_config_payload({"enable_ai_agents": True})

    assert payload["enable_ai_agents"] is True
    assert persisted_updates[0]["enable_ai_agents"] is True
    assert persisted_updates[0]["model_novelist_analysis"]["name"] == "gpt-5-nano"
    assert persisted_updates[0]["model_novelist_writer"]["name"] == "gpt-5-nano"


@pytest.mark.asyncio
async def test_architect_generation_is_blocked_when_agents_disabled(monkeypatch) -> None:
    monkeypatch.setattr(agent_feature_gate, "get_settings", lambda: SimpleNamespace(enable_ai_agents=False))

    with pytest.raises(HTTPException) as exc_info:
        await architect.generate_entities_from_validated_proposals(
            run_id="run-1",
            payload=object(),
            current_user=object(),
            service=object(),
        )

    assert exc_info.value.status_code == 503
    assert "Enable Agents" in str(exc_info.value.detail)
