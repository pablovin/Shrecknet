from __future__ import annotations

from types import MethodType

import pytest

from app.config_store import ProviderDefaults, ProviderState, RuntimeConfig
from app.provider_registry import ProviderRegistry
from app.service import ChatService


class FakeAdapter:
    def __init__(self, provider_id: str, models: list[str], *, chat_error: Exception | None = None) -> None:
        self.provider_id = provider_id
        self._models = models
        self.chat_error = chat_error
        self.chat_calls: list[dict[str, object]] = []

    async def list_models(self) -> list[str]:
        return self._models

    async def chat(self, **kwargs):
        self.chat_calls.append(kwargs)
        if self.chat_error is not None:
            raise self.chat_error
        return {"text": "pong", "usage": {}}


def _service(runtime: RuntimeConfig, adapters: list[FakeAdapter]) -> ChatService:
    service = ChatService.__new__(ChatService)
    service._runtime = runtime
    service._openai = None
    service._anthropic = None
    service.registry = ProviderRegistry()
    for adapter in adapters:
        service.registry.register(adapter)

    async def _persist_provider_validation(self, provider_id: str, validation: dict[str, object]) -> RuntimeConfig:
        active = ChatService._validation_succeeded(validation)
        reason = None if active else str(validation.get("reason") or validation.get("error") or "provider validation failed")
        self._runtime.provider_states[provider_id] = ProviderState(
            active=active,
            last_validation_error=reason,
            last_error=reason,
        )
        return self._runtime

    service._persist_provider_validation = MethodType(_persist_provider_validation, service)
    return service


@pytest.mark.asyncio
async def test_provider_status_includes_base_url_and_api_key_presence() -> None:
    service = _service(
        RuntimeConfig(
            provider_defaults={
                "openai": ProviderDefaults(
                    kind="cloud",
                    auth_strategy="api_key",
                    models=["gpt-5-nano"],
                    base_url="https://api.openai.example/v1",
                    api_key="sk-secret",
                )
            },
            provider_states={"openai": ProviderState(active=True)},
        ),
        [FakeAdapter("openai", ["gpt-5-nano"])],
    )

    payload = await service.provider_validation_status("openai")

    assert payload["base_url"] == "https://api.openai.example/v1"
    assert payload["api_key_present"] is True


@pytest.mark.asyncio
async def test_models_only_returns_active_providers() -> None:
    service = _service(
        RuntimeConfig(
            provider_defaults={
                "openai": ProviderDefaults(models=["gpt-5-nano"], api_key="sk-secret"),
                "ollama_cloud": ProviderDefaults(models=["old-cloud-model"], api_key="cloud-secret"),
            },
            provider_states={
                "openai": ProviderState(active=True),
                "ollama_cloud": ProviderState(active=False),
            },
        ),
        [
            FakeAdapter("openai", ["gpt-5-nano", "gpt-5"]),
            FakeAdapter("ollama_cloud", ["new-cloud-model"]),
        ],
    )

    payload = await service.models()

    assert sorted(payload["providers"].keys()) == ["openai"]
    assert payload["providers"]["openai"]["models"] == ["gpt-5-nano", "gpt-5"]


@pytest.mark.asyncio
async def test_models_uses_cloud_fallbacks_when_discovery_is_empty() -> None:
    service = _service(
        RuntimeConfig(
            provider_defaults={
                "openai": ProviderDefaults(models=["gpt-5-nano"], api_key="sk-secret"),
                "anthropic": ProviderDefaults(models=["claude-3-haiku-20240307"], api_key="sk-ant-secret"),
                "ollama_cloud": ProviderDefaults(models=["old-cloud-model"], api_key="cloud-secret"),
            },
            provider_states={
                "openai": ProviderState(active=True),
                "anthropic": ProviderState(active=True),
                "ollama_cloud": ProviderState(active=True),
            },
        ),
        [
            FakeAdapter("openai", []),
            FakeAdapter("anthropic", []),
            FakeAdapter("ollama_cloud", []),
        ],
    )

    payload = await service.models()

    assert payload["providers"]["openai"]["models"] == ["gpt-5-nano", "gpt-5", "gpt-4o-mini"]
    assert payload["providers"]["anthropic"]["models"] == [
        "claude-3-haiku-20240307",
        "claude-opus-4-1-20250805",
    ]
    assert payload["providers"]["ollama_cloud"]["models"] == [
        "old-cloud-model",
        "gemma4:31b",
        "gemma4:31b-cloud",
    ]


@pytest.mark.asyncio
async def test_cloud_provider_without_api_key_cannot_read_as_active() -> None:
    service = _service(
        RuntimeConfig(
            provider_defaults={
                "anthropic": ProviderDefaults(
                    kind="cloud",
                    auth_strategy="none",
                    models=["claude-3-haiku-20240307"],
                    base_url="https://api.anthropic.com",
                    api_key="",
                )
            },
            provider_states={"anthropic": ProviderState(active=True)},
        ),
        [FakeAdapter("anthropic", ["claude-3-haiku-20240307"])],
    )

    payload = await service.provider_validation_status("anthropic")

    assert payload["active"] is False
    assert payload["api_key_present"] is False
    assert payload["reason"] == "missing_api_key"
    assert payload["models"][0]["available"] is False


@pytest.mark.asyncio
async def test_models_omits_cloud_provider_without_api_key_even_if_state_is_active() -> None:
    service = _service(
        RuntimeConfig(
            provider_defaults={
                "openai": ProviderDefaults(
                    kind="cloud",
                    auth_strategy="none",
                    models=["gpt-5-nano"],
                    api_key="",
                )
            },
            provider_states={"openai": ProviderState(active=True)},
        ),
        [FakeAdapter("openai", ["gpt-5-nano"])],
    )

    payload = await service.models()

    assert payload["providers"] == {}


@pytest.mark.asyncio
async def test_revalidate_all_providers_runs_functional_ping_before_marking_active() -> None:
    adapter = FakeAdapter("ollama_cloud", ["gemma4:31b"])
    service = _service(
        RuntimeConfig(
            provider_defaults={
                "ollama_cloud": ProviderDefaults(
                    kind="cloud",
                    auth_strategy="api_key",
                    models=["gemma4:31b"],
                    base_url="https://ollama.com",
                    api_key="cloud-secret",
                )
            },
            provider_states={"ollama_cloud": ProviderState(active=False)},
        ),
        [adapter],
    )

    payload = await service.revalidate_all_providers()

    assert len(adapter.chat_calls) == 1
    assert adapter.chat_calls[0]["model"] == "gemma4:31b"
    assert payload["providers"]["ollama_cloud"]["active"] is True


@pytest.mark.asyncio
async def test_revalidate_all_providers_deactivates_provider_when_ping_fails() -> None:
    service = _service(
        RuntimeConfig(
            provider_defaults={
                "ollama_cloud": ProviderDefaults(
                    kind="cloud",
                    auth_strategy="api_key",
                    models=["gemma4:31b"],
                    base_url="https://ollama.com",
                    api_key="cloud-secret",
                )
            },
            provider_states={"ollama_cloud": ProviderState(active=True)},
        ),
        [FakeAdapter("ollama_cloud", ["gemma4:31b"], chat_error=RuntimeError("provider rejected ping"))],
    )

    payload = await service.revalidate_all_providers()

    assert payload["providers"]["ollama_cloud"]["active"] is False
    assert payload["providers"]["ollama_cloud"]["reason"] == "provider rejected ping"
