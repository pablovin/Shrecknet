from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import router
from app.config import Settings
from app.schemas import ChatRequest, ChatResponse, ChatUsage
from app.service import ChatService


class FakeService:
    def __init__(self) -> None:
        self.calls = 0

    async def health(self):
        return {"ok": True, "service": "shreckLLM"}

    async def ready(self):
        return {
            "ready": True,
            "dependencies": {
                "ollama": {"ok": True, "default_model_available": True},
                "openai": {"ok": True, "default_model_available": True},
                "anthropic": {"ok": True, "default_model_available": True},
                "redis": {"ok": True},
            },
        }

    async def models(self):
        return {
            "default_provider_id": "ollama",
            "providers": {
                "ollama": {"default_model": "gemma3:4b", "models": ["gemma3:4b"]},
                "openai": {"default_model": "gpt-5-nano", "models": ["gpt-5-nano"]},
                "anthropic": {"default_model": "claude-3-haiku-20240307", "models": ["claude-3-haiku-20240307"]},
            },
        }

    async def status(self):
        from app.schemas import ServiceStatusResponse

        return ServiceStatusResponse(
            default_provider_id="ollama",
            redis_url="redis://redis:6379/2",
            in_flight_requests=0,
            waiting_requests=0,
            max_concurrent_requests=8,
            request_timeout_seconds=45,
            max_queue_wait_seconds=10,
            dependencies={
                "ollama": {"ok": True, "default_model_available": True},
                "openai": {"ok": True, "default_model_available": True},
                "anthropic": {"ok": True, "default_model_available": True},
                "redis": {"ok": True},
            },
        )

    async def chat(self, request: ChatRequest):
        self.calls += 1
        await asyncio.sleep(0.01)
        model = request.model or "gemma3:4b"
        return ChatResponse(
            text=f"echo:{request.messages[-1].content}",
            provider_id=request.provider_id,
            requested_model=request.model,
            resolved_model=model,
            provider_request_id=None,
            model=model,
            usage=ChatUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            latency_ms=10,
            conversation_id=request.conversation_id,
            memory_applied=bool(request.use_conversation_memory and request.conversation_id),
            metadata=request.metadata,
        )

    def config_public_view(self):
        return {
            "default_provider_id": "ollama",
            "provider_defaults": {
                "ollama": {"default_model": "gemma3:4b", "base_url": "http://host.docker.internal:11434", "api_key": None},
                "openai": {"default_model": "gpt-5-nano", "base_url": None, "api_key": "sk-...mask"},
                "anthropic": {"default_model": "claude-3-haiku-20240307", "base_url": "https://api.anthropic.com", "api_key": "sk-ant-...mask"},
            },
        }

    async def refresh_runtime(self):
        return None

    async def openai_validation_status(self):
        return {"configured": True, "present": True, "valid": True, "error": None}

    async def anthropic_validation_status(self):
        return {"configured": True, "present": True, "valid": True, "error": None}


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
async def test_chat_endpoint_and_concurrency(test_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        payload = {
            "provider_id": "ollama",
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
                    json={"provider_id": "ollama", "messages": [{"role": "user", "content": f"msg-{i}"}]},
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
            resolved_model=request.model or "gemma3:4b",
            provider_request_id=None,
            model=request.model or "gemma3:4b",
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
