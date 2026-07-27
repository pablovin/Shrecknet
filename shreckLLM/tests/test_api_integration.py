from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import router
from app.config import Settings
from app.config_store import ProviderDefaults
from app.schemas import ChatRequest, ChatResponse, ChatUsage
from app.service import ChatService


class FakeService:
    def __init__(self) -> None:
        self.calls = 0
        self._runtime = type(
            "Runtime",
            (),
            {
                "provider_defaults": {
                    "ollama": ProviderDefaults(
                        kind="local",
                        auth_strategy="none",
                        healthcheck_path="/api/tags",
                        models=["gemma3:4b"],
                        base_url="http://host.docker.internal:11434",
                        api_key=None,
                    )
                },
                "provider_states": {},
            },
        )()

    async def health(self):
        return {"ok": True, "service": "shreckLLM"}

    async def ready(self):
        return {
            "ready": True,
            "dependencies": {
                "ollama": {"ok": True, "any_configured_model_available": True},
                "openai": {"ok": True, "any_configured_model_available": True},
                "anthropic": {"ok": True, "any_configured_model_available": True},
                "redis": {"ok": True},
            },
        }

    async def models(self):
        return {
            "providers": {
                "ollama": {"models": ["gemma3:4b"]},
                "openai": {"models": ["gpt-5-nano"]},
                "anthropic": {"models": ["claude-3-haiku-20240307"]},
            },
        }

    async def status(self):
        from app.schemas import ServiceStatusResponse

        return ServiceStatusResponse(
            shreckllm_operational=True,
            operational_provider_ids=["ollama"],
            providers_summary={
                "total": 1,
                "active": 1,
                "inactive": 0,
                "provider_ids": ["ollama"],
                "active_provider_ids": ["ollama"],
            },
            redis_url="redis://redis:6379/2",
            in_flight_requests=0,
            waiting_requests=0,
            max_concurrent_requests=8,
            request_timeout_seconds=45,
            max_queue_wait_seconds=10,
            dependencies={
                "ollama": {"ok": True, "any_configured_model_available": True},
                "openai": {"ok": True, "any_configured_model_available": True},
                "anthropic": {"ok": True, "any_configured_model_available": True},
                "redis": {"ok": True},
            },
        )

    async def chat(self, request: ChatRequest):
        self.calls += 1
        await asyncio.sleep(0.01)
        return ChatResponse(
            text=f"echo:{request.messages[-1].content}",
            provider_id=request.provider_id,
            requested_model=request.model,
            resolved_model=request.model,
            provider_request_id=None,
            model=request.model,
            usage=ChatUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            latency_ms=10,
            conversation_id=request.conversation_id,
            memory_applied=bool(request.use_conversation_memory and request.conversation_id),
            metadata=request.metadata,
        )

    def config_public_view(self):
        return {
            "provider_defaults": {
                "ollama": {"models": ["gemma3:4b"], "base_url": "http://host.docker.internal:11434", "api_key": None},
                "openai": {"models": ["gpt-5-nano"], "base_url": None, "api_key": "sk-...mask"},
                "anthropic": {"models": ["claude-3-haiku-20240307"], "base_url": "https://api.anthropic.com", "api_key": "sk-ant-...mask"},
            },
            "provider_states": {
                "ollama": {"active": False, "last_validated_at": None, "last_validation_checked_at": None, "last_validation_failed_at": None, "last_validation_error": None, "last_warmed_at": None, "last_error": None},
                "openai": {"active": False, "last_validated_at": None, "last_validation_checked_at": None, "last_validation_failed_at": None, "last_validation_error": None, "last_warmed_at": None, "last_error": None},
                "anthropic": {"active": False, "last_validated_at": None, "last_validation_checked_at": None, "last_validation_failed_at": None, "last_validation_error": None, "last_warmed_at": None, "last_error": None},
            },
        }

    def runtime_config_public_view(self):
        return {
            "memory_ttl_seconds": 3600,
            "memory_max_messages": 24,
            "max_concurrent_requests": 8,
            "request_timeout_seconds": 180.0,
            "max_queue_wait_seconds": 10.0,
            "provider_limits": {},
            "chat_job_queue_max_size": 256,
            "chat_job_result_ttl_seconds": 900,
            "chat_job_poll_default_interval_ms": 250,
            "chat_job_max_retries": 2,
        }

    async def refresh_runtime(self):
        return None

    async def openai_validation_status(self):
        return {"configured": True, "present": True, "valid": True, "error": None}

    async def anthropic_validation_status(self):
        return {"configured": True, "present": True, "valid": True, "error": None}

    async def all_provider_validation_statuses(self):
        return {
            "shreckllm_operational": True,
            "operational_provider_ids": ["ollama"],
            "providers": {
                "ollama": {
                    "provider_id": "ollama",
                    "active": True,
                    "models": [{"model": "gemma3:4b", "available": True}],
                }
            },
        }

    async def provider_validation_status(self, provider_id: str):
        payload = (await self.all_provider_validation_statuses())["providers"].get(provider_id)
        if payload is None:
            return {"provider_id": provider_id, "active": False, "reason": "provider_not_configured"}
        return payload

    async def validate_provider_models(self, provider_id: str, cfg):
        discovered_models = {
            "ollama": ["gemma3:4b", "llama3.2:3b"],
            "openai": ["gpt-5-nano"],
            "anthropic": ["claude-3-haiku-20240307"],
        }.get(provider_id, [])
        configured_models = []
        for model in cfg.models:
            cleaned = str(model).strip()
            if cleaned and cleaned not in configured_models:
                configured_models.append(cleaned)
        invalid_models = [model for model in configured_models if model not in discovered_models]
        return {
            "valid": bool(configured_models) and not invalid_models,
            "error": "provider_requires_model" if not configured_models else ("invalid_provider_models" if invalid_models else None),
            "provider_id": provider_id,
            "configured_models": configured_models,
            "discovered_models": discovered_models,
            "invalid_models": invalid_models,
        }

    async def refresh_runtime_and_validate(self, provider_ids=None, *, ping: bool = True):
        del provider_ids
        del ping
        return await self.all_provider_validation_statuses()

    async def activate_provider(self, provider_id: str):
        return {"provider_id": provider_id, "active": True}

    async def deactivate_provider(self, provider_id: str):
        return {"provider_id": provider_id, "active": False}

    async def test_provider_functionality(self, provider_id: str, *, ping: bool = True):
        return {
            "provider": {"provider_id": provider_id, "active": True},
            "shreckllm_operational": True,
            "operational_provider_ids": [provider_id],
        }


class _FakeProviderRegistry:
    def __init__(self, adapters):
        self._adapters = adapters

    def get(self, provider_id: str):
        return self._adapters.get(provider_id)

    def provider_ids(self):
        return sorted(self._adapters.keys())


class _FakeProviderAdapter:
    provider_id = "ollama"

    async def list_models(self):
        return ["gemma3:4b", "llama3.2:3b"]


@pytest.fixture
def test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.chat_service = FakeService()
    return app


@pytest.mark.asyncio
async def test_health_ready_models_status(test_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/ready")).status_code == 200
        assert (await client.get("/models")).status_code == 200
        assert (await client.get("/status")).status_code == 200


@pytest.mark.asyncio
async def test_status_includes_provider_summary() -> None:
    from app.api import status_payload

    body = await status_payload(service=FakeService())
    assert body.shreckllm_operational is True
    assert body.providers_summary["active_provider_ids"] == ["ollama"]


@pytest.mark.asyncio
async def test_config_routes_exclude_provider_fields(monkeypatch) -> None:
    import app.api as api
    from app.api import get_config, put_config
    from app.config_store import RuntimeConfigUpdate

    service = FakeService()
    monkeypatch.setattr(api, "update_runtime_config", lambda patch: service._runtime)
    monkeypatch.setattr(api, "reload_runtime_config", lambda: service._runtime)

    config = await get_config(service=service, _user=object())
    assert "provider_defaults" not in config
    assert "provider_states" not in config

    updated = await put_config(
        payload=RuntimeConfigUpdate(
            request_timeout_seconds=120.0,
            provider_defaults={"openai": ProviderDefaults(models=["gpt-5-nano"])},
        ),
        service=service,
        _user=object(),
    )
    assert "provider_defaults" not in updated
    assert "provider_states" not in updated


@pytest.mark.asyncio
async def test_provider_routes_return_canonical_payloads() -> None:
    from app.api import get_provider, get_providers

    service = FakeService()

    providers = await get_providers(service=service)
    assert providers["shreckllm_operational"] is True
    assert providers["providers"]["ollama"]["active"] is True

    provider = await get_provider(provider_id="ollama", service=service, _user=object())
    assert provider["provider_id"] == "ollama"
    assert provider["active"] is True


@pytest.mark.asyncio
async def test_put_provider_updates_and_returns_operational_payload(monkeypatch) -> None:
    import app.api as api
    from app.api import ProviderMetadataUpdateRequest, put_provider

    service = FakeService()
    monkeypatch.setattr(api, "update_runtime_config", lambda patch: service._runtime)
    monkeypatch.setattr(api, "reload_runtime_config", lambda: service._runtime)

    body = await put_provider(
        provider_id="ollama",
        payload=ProviderMetadataUpdateRequest(base_url="http://localhost:11434", models=["gemma3:4b"]),
        service=service,
        _user=object(),
    )

    assert body["provider"]["provider_id"] == "ollama"
    assert body["shreckllm_operational"] is True


@pytest.mark.asyncio
async def test_put_provider_rejects_empty_models_before_persisting(monkeypatch) -> None:
    import app.api as api
    from app.api import ProviderMetadataUpdateRequest, put_provider
    from fastapi import HTTPException

    service = FakeService()
    updates = []
    monkeypatch.setattr(api, "update_runtime_config", lambda patch: updates.append(patch))
    monkeypatch.setattr(api, "reload_runtime_config", lambda: service._runtime)

    with pytest.raises(HTTPException) as exc_info:
        await put_provider(
            provider_id="ollama",
            payload=ProviderMetadataUpdateRequest(models=[]),
            service=service,
            _user=object(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "provider_requires_model"
    assert updates == []


@pytest.mark.asyncio
async def test_put_provider_rejects_unknown_model_before_persisting(monkeypatch) -> None:
    import app.api as api
    from app.api import ProviderMetadataUpdateRequest, put_provider
    from fastapi import HTTPException

    service = FakeService()
    updates = []
    monkeypatch.setattr(api, "update_runtime_config", lambda patch: updates.append(patch))
    monkeypatch.setattr(api, "reload_runtime_config", lambda: service._runtime)

    with pytest.raises(HTTPException) as exc_info:
        await put_provider(
            provider_id="ollama",
            payload=ProviderMetadataUpdateRequest(models=["missing-model"]),
            service=service,
            _user=object(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "invalid_provider_models"
    assert exc_info.value.detail["invalid_models"] == ["missing-model"]
    assert updates == []


@pytest.mark.asyncio
async def test_put_provider_persists_normalized_valid_models(monkeypatch) -> None:
    import app.api as api
    from app.api import ProviderMetadataUpdateRequest, put_provider

    service = FakeService()
    updates = []
    monkeypatch.setattr(api, "update_runtime_config", lambda patch: updates.append(patch))
    monkeypatch.setattr(api, "reload_runtime_config", lambda: service._runtime)

    await put_provider(
        provider_id="ollama",
        payload=ProviderMetadataUpdateRequest(models=[" gemma3:4b ", "gemma3:4b", "llama3.2:3b"]),
        service=service,
        _user=object(),
    )

    saved = updates[0]["provider_defaults"]["ollama"]
    assert saved.models == ["gemma3:4b", "llama3.2:3b"]


@pytest.mark.asyncio
async def test_add_provider_model_rejects_unknown_model_before_persisting(monkeypatch) -> None:
    import app.api as api
    from app.api import ProviderModelMutationRequest, add_provider_model
    from fastapi import HTTPException

    service = FakeService()
    updates = []
    monkeypatch.setattr(api, "update_runtime_config", lambda patch: updates.append(patch))
    monkeypatch.setattr(api, "reload_runtime_config", lambda: service._runtime)

    with pytest.raises(HTTPException) as exc_info:
        await add_provider_model(
            provider_id="ollama",
            payload=ProviderModelMutationRequest(model="missing-model"),
            service=service,
            _user=object(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "invalid_provider_models"
    assert updates == []


@pytest.mark.asyncio
async def test_models_catalog_exposes_configured_discovered_and_merged_models() -> None:
    service = object.__new__(ChatService)
    service.registry = _FakeProviderRegistry({"ollama": _FakeProviderAdapter()})
    service._runtime = type(
        "Runtime",
        (),
        {"provider_defaults": {"ollama": ProviderDefaults(models=["gemma3:4b"])}},
    )()

    body = await service.models()

    assert body["providers"]["ollama"]["configured_models"] == ["gemma3:4b"]
    assert body["providers"]["ollama"]["discovered_models"] == ["gemma3:4b", "llama3.2:3b"]
    assert body["providers"]["ollama"]["models"] == ["gemma3:4b", "llama3.2:3b"]


@pytest.mark.asyncio
async def test_providers_validate_exposes_operational_flag() -> None:
    from app.api import get_providers_validate

    body = await get_providers_validate(service=FakeService(), _user=object())
    assert body["shreckllm_operational"] is True
    assert body["operational_provider_ids"] == ["ollama"]
    assert "providers" in body


def test_operational_flag_false_without_valid_provider() -> None:
    providers = {
        "ollama": {
            "provider_id": "ollama",
            "active": False,
            "models": [{"model": "gemma3:4b", "available": False}],
        }
    }

    assert ChatService._operational_provider_ids(providers) == []


def test_operational_flag_true_with_valid_provider() -> None:
    providers = {
        "ollama": {
            "provider_id": "ollama",
            "active": True,
            "models": [{"model": "gemma3:4b", "available": True}],
        },
        "openai": {
            "provider_id": "openai",
            "active": False,
            "models": [{"model": "gpt-5-nano", "available": False}],
        },
    }

    assert ChatService._operational_provider_ids(providers) == ["ollama"]


def test_validation_passed_overrides_previous_active_state() -> None:
    assert ChatService._validation_succeeded({"active": True, "validation_passed": False}) is False
    assert ChatService._validation_succeeded({"active": False, "validation_passed": True}) is True
    assert ChatService._validation_succeeded({"active": True}) is True


@pytest.mark.asyncio
async def test_provider_test_route_returns_operational_payload() -> None:
    from app.api import test_provider_functionality

    body = await test_provider_functionality(provider_id="openai", service=FakeService(), _user=object())

    assert body["provider"]["provider_id"] == "openai"
    assert body["provider"]["active"] is True
    assert body["shreckllm_operational"] is True


@pytest.mark.asyncio
async def test_chat_endpoint_and_concurrency(test_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        payload = {
            "provider_id": "ollama",
            "model": "gemma3:4b",
            "messages": [{"role": "user", "content": "hello"}],
            "conversation_id": "abc",
            "use_conversation_memory": True,
        }
        response = await client.post("/chat", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["text"] == "echo:hello"
        assert body["memory_applied"] is True
        assert body["provider_id"] == "ollama"

        bad = await client.post("/chat", json={"messages": [{"role": "user", "content": "x"}]})
        assert bad.status_code == 422

        burst = await asyncio.gather(
            *(
                client.post(
                    "/chat",
                    json={
                        "provider_id": "ollama",
                        "model": "gemma3:4b",
                        "messages": [{"role": "user", "content": f"msg-{i}"}],
                    },
                )
                for i in range(12)
            )
        )
        assert all(r.status_code == 200 for r in burst)


@pytest.mark.asyncio
async def test_chat_jobs_continue_after_runtime_refresh(monkeypatch, tmp_path) -> None:
    import app.config_store as config_store

    settings = Settings(
        redis_url="redis://localhost:6379/15",
        data_dir=str(tmp_path),
        ollama_prewarm_on_startup=False,
    )
    config_store._cache = None
    monkeypatch.setattr(config_store, "get_settings", lambda: settings)

    service = ChatService(settings)

    async def fake_execute(request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            text="ok",
            provider_id=request.provider_id,
            requested_model=request.model,
            resolved_model=request.model,
            provider_request_id=None,
            model=request.model,
            usage=ChatUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            latency_ms=1.0,
            conversation_id=request.conversation_id,
            memory_applied=False,
            metadata=request.metadata,
        )

    monkeypatch.setattr(service, "_execute_chat_request", fake_execute)

    try:
        service.ensure_background_tasks()
        await service.refresh_runtime()
        await service._persist_provider_validation(
            "ollama",
            {"provider_id": "ollama", "active": True, "reason": None},
        )

        request = ChatRequest(
            provider_id="ollama",
            model="gemma3:4b",
            messages=[{"role": "user", "content": "hello"}],
        )
        job = await service.submit_chat_job(request)
        result = await service.wait_for_chat_job_result(job.job_id, timeout_s=1.0)

        assert result.text == "ok"
        assert service.get_chat_job_status(job.job_id).status == "succeeded"
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_chat_job_worker_pool_executes_independent_jobs_concurrently(monkeypatch, tmp_path) -> None:
    import app.config_store as config_store

    settings = Settings(
        redis_url="redis://localhost:6379/15",
        data_dir=str(tmp_path),
        bootstrap_max_concurrent_requests=2,
        ollama_prewarm_on_startup=False,
    )
    config_store._cache = None
    monkeypatch.setattr(config_store, "get_settings", lambda: settings)
    service = ChatService(settings)
    both_started = asyncio.Event()
    release = asyncio.Event()
    running = 0

    async def fake_execute(request: ChatRequest) -> ChatResponse:
        nonlocal running
        running += 1
        if running == 2:
            both_started.set()
        await release.wait()
        return ChatResponse(
            text="ok",
            provider_id=request.provider_id,
            requested_model=request.model,
            resolved_model=request.model,
            provider_request_id=None,
            model=request.model,
            usage=ChatUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            latency_ms=1.0,
            conversation_id=None,
            memory_applied=False,
            metadata=None,
        )

    monkeypatch.setattr(service, "_execute_chat_request", fake_execute)
    monkeypatch.setattr(service, "_effective_provider_active", lambda *_args: (True, None))
    request = ChatRequest(provider_id="ollama", model="gemma3:4b", messages=[{"role": "user", "content": "hello"}])
    try:
        first = await service.submit_chat_job(request)
        second = await service.submit_chat_job(request)
        await asyncio.wait_for(both_started.wait(), timeout=1.0)
        release.set()
        await asyncio.gather(
            service.wait_for_chat_job_result(first.job_id, timeout_s=1.0),
            service.wait_for_chat_job_result(second.job_id, timeout_s=1.0),
        )
        assert service.get_chat_job_status(first.job_id).queue_wait_ms is not None
        assert service.get_chat_job_status(second.job_id).execution_ms is not None
    finally:
        release.set()
        await service.aclose()


@pytest.mark.asyncio
async def test_prewarm_only_runs_for_active_providers(monkeypatch, tmp_path) -> None:
    import app.config_store as config_store

    settings = Settings(
        redis_url="redis://localhost:6379/15",
        data_dir=str(tmp_path),
        ollama_prewarm_on_startup=True,
    )
    config_store._cache = None
    monkeypatch.setattr(config_store, "get_settings", lambda: settings)

    service = ChatService(settings)

    inactive_calls: list[str] = []

    async def inactive_chat(**kwargs):
        inactive_calls.append(kwargs["model"])
        return None

    try:
        assert service._ollama is not None
        monkeypatch.setattr(service._ollama, "chat", inactive_chat)
        async def fake_validation(_provider_id: str):
            return {
                "provider_id": "ollama",
                "active": False,
                "reason": None,
            }

        monkeypatch.setattr(service, "provider_validation_status", fake_validation)
        await service.prewarm_active_providers()
        assert inactive_calls == []

        await service._set_provider_state("ollama", active=True, last_error=None)

        active_calls: list[str] = []

        async def active_chat(**kwargs):
            active_calls.append(kwargs["model"])
            return None

        assert service._ollama is not None
        monkeypatch.setattr(service._ollama, "chat", active_chat)
        await service.prewarm_active_providers()
        assert active_calls == [service._runtime.provider_defaults["ollama"].models[0]]
    finally:
        await service.aclose()
