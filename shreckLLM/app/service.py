from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from redis.asyncio import Redis

from app.concurrency import RequestLimiter
from app.config import Settings
from app.config_store import RuntimeConfig, get_runtime_config
from app.errors import DependencyUnavailableError, InvalidModelError
from app.locking import ConversationLockManager
from app.memory import RedisConversationMemory
from app.anthropic_client import AnthropicClient
from app.ollama_client import OllamaClient
from app.openai_client import OpenAIClient
from app.provider_registry import ProviderRegistry
from app.schemas import ChatMessage, ChatRequest, ChatResponse, ChatUsage, ServiceStatusResponse

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.redis = Redis.from_url(settings.redis_url, decode_responses=False)
        self.locks = ConversationLockManager()
        self._runtime = get_runtime_config()

        self.memory = RedisConversationMemory(
            self.redis,
            ttl_seconds=self._runtime.memory_ttl_seconds,
            max_messages=self._runtime.memory_max_messages,
        )
        self.registry = ProviderRegistry()
        self._ollama: OllamaClient | None = None
        self._openai: OpenAIClient | None = None
        self._anthropic: AnthropicClient | None = None
        self._bind_providers()
        self.limiter = RequestLimiter(max_concurrent=self._runtime.max_concurrent_requests)

    def _bind_providers(self) -> None:
        self.registry = ProviderRegistry()
        provider_defaults = self._runtime.provider_defaults

        ollama_cfg = provider_defaults.get("ollama")
        if ollama_cfg is not None:
            self._ollama = OllamaClient(
                base_url=ollama_cfg.base_url or "http://localhost:11434",
                timeout_s=self._runtime.request_timeout_seconds,
            )
            self.registry.register(self._ollama)
        else:
            self._ollama = None

        openai_cfg = provider_defaults.get("openai")
        if openai_cfg is not None:
            self._openai = OpenAIClient(
                api_key=openai_cfg.api_key or "",
                timeout_s=self._runtime.request_timeout_seconds,
                base_url=openai_cfg.base_url,
            )
            self.registry.register(self._openai)
        else:
            self._openai = None

        anthropic_cfg = provider_defaults.get("anthropic")
        if anthropic_cfg is not None:
            self._anthropic = AnthropicClient(
                api_key=anthropic_cfg.api_key or "",
                timeout_s=self._runtime.request_timeout_seconds,
                base_url=anthropic_cfg.base_url,
            )
            self.registry.register(self._anthropic)
        else:
            self._anthropic = None

    async def aclose(self) -> None:
        closers = [self.redis.aclose()]
        if self._ollama is not None:
            closers.append(self._ollama.aclose())
        if self._openai is not None:
            closers.append(self._openai.aclose())
        if self._anthropic is not None:
            closers.append(self._anthropic.aclose())
        await asyncio.gather(*closers, return_exceptions=True)

    async def refresh_runtime(self) -> RuntimeConfig:
        self._runtime = get_runtime_config()
        closers = []
        if self._ollama is not None:
            closers.append(self._ollama.aclose())
        if self._openai is not None:
            closers.append(self._openai.aclose())
        if self._anthropic is not None:
            closers.append(self._anthropic.aclose())
        if closers:
            await asyncio.gather(*closers, return_exceptions=True)

        self.memory = RedisConversationMemory(
            self.redis,
            ttl_seconds=self._runtime.memory_ttl_seconds,
            max_messages=self._runtime.memory_max_messages,
        )
        self._bind_providers()
        self.limiter = RequestLimiter(max_concurrent=self._runtime.max_concurrent_requests)
        return self._runtime

    @staticmethod
    def _mask_secret(value: str | None) -> str | None:
        if value is None:
            return None
        raw = str(value)
        if not raw:
            return ""
        if len(raw) <= 8:
            return "*" * len(raw)
        return f"{raw[:4]}...{raw[-4:]}"

    def config_public_view(self) -> dict[str, Any]:
        payload = self._runtime.model_dump()
        providers = payload.get("provider_defaults") or {}
        if isinstance(providers, dict):
            openai_cfg = providers.get("openai")
            if isinstance(openai_cfg, dict):
                openai_cfg["api_key"] = self._mask_secret(openai_cfg.get("api_key"))
            anthropic_cfg = providers.get("anthropic")
            if isinstance(anthropic_cfg, dict):
                anthropic_cfg["api_key"] = self._mask_secret(anthropic_cfg.get("api_key"))
        return payload

    async def openai_validation_status(self) -> dict[str, Any]:
        if self._openai is None:
            return {
                "configured": False,
                "present": False,
                "valid": None,
                "error": "provider_not_registered",
            }
        return await self._openai.validate_api_key()

    async def anthropic_validation_status(self) -> dict[str, Any]:
        if self._anthropic is None:
            return {
                "configured": False,
                "present": False,
                "valid": None,
                "error": "provider_not_registered",
            }
        return await self._anthropic.validate_api_key()

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "service": "shreckLLM"}

    async def ready(self) -> dict[str, Any]:
        redis_ok = await self.memory.ping()
        dependencies: dict[str, Any] = {"redis": {"ok": redis_ok}}

        provider_ready = False
        for provider_id in self.registry.provider_ids():
            adapter = self.registry.get(provider_id)
            if adapter is None:
                continue
            ok = await adapter.health()
            model_ok = False
            default_model = None
            cfg = self._runtime.provider_defaults.get(provider_id)
            if cfg is not None:
                default_model = cfg.default_model
                try:
                    model_ok = default_model in set(await adapter.list_models())
                except Exception:
                    model_ok = False
            dependencies[provider_id] = {
                "ok": bool(ok),
                "default_model": default_model,
                "default_model_available": model_ok,
            }
            provider_ready = provider_ready or (bool(ok) and bool(model_ok))

        return {
            "ready": redis_ok and provider_ready,
            "dependencies": dependencies,
        }

    async def models(self) -> dict[str, Any]:
        providers_payload: dict[str, Any] = {}
        for provider_id in self.registry.provider_ids():
            adapter = self.registry.get(provider_id)
            if adapter is None:
                continue
            cfg = self._runtime.provider_defaults.get(provider_id)
            try:
                discovered_models = await adapter.list_models()
            except Exception:
                discovered_models = []
            configured_models = list(cfg.models) if cfg else []
            merged_models: list[str] = []
            for model in [*configured_models, *discovered_models]:
                if isinstance(model, str) and model and model not in merged_models:
                    merged_models.append(model)
            providers_payload[provider_id] = {
                "default_model": cfg.default_model if cfg else None,
                "configured_models": configured_models,
                "discovered_models": discovered_models,
                "models": merged_models,
            }

        return {
            "default_provider_id": self._runtime.default_provider_id,
            "providers": providers_payload,
        }

    async def status(self) -> ServiceStatusResponse:
        ready_payload = await self.ready()
        return ServiceStatusResponse(
            default_provider_id=self._runtime.default_provider_id,
            redis_url=self.settings.redis_url,
            in_flight_requests=self.limiter.in_flight,
            waiting_requests=self.limiter.waiting,
            max_concurrent_requests=self._runtime.max_concurrent_requests,
            request_timeout_seconds=self._runtime.request_timeout_seconds,
            max_queue_wait_seconds=self._runtime.max_queue_wait_seconds,
            dependencies=ready_payload["dependencies"],
        )

    async def chat(self, request: ChatRequest) -> ChatResponse:
        start = time.monotonic()

        async with self.limiter.slot(wait_timeout_s=self._runtime.max_queue_wait_seconds):
            memory_applied = bool(request.use_conversation_memory and request.conversation_id)
            if memory_applied and request.conversation_id:
                lock = await self.locks.get_lock(request.conversation_id)
                async with lock:
                    return await self._chat_with_memory_lock(request, start)

            result = await self._run_chat(request, history=[])
            latency_ms = round((time.monotonic() - start) * 1000, 2)
            resolved_model = result["resolved_model"]
            return ChatResponse(
                text=result["result"]["text"],
                provider_id=result["provider_id"],
                requested_model=result["requested_model"],
                resolved_model=resolved_model,
                provider_request_id=result["result"].get("provider_request_id"),
                model=resolved_model,
                usage=ChatUsage.model_validate(result["result"]["usage"]),
                latency_ms=latency_ms,
                conversation_id=request.conversation_id,
                memory_applied=False,
                metadata=request.metadata,
            )

    async def _chat_with_memory_lock(self, request: ChatRequest, start: float) -> ChatResponse:
        if not request.conversation_id:
            raise InvalidModelError("conversation_id required for memory mode")

        history = await self.memory.load(request.conversation_id)
        result = await self._run_chat(request, history=history)

        assistant_message = ChatMessage(role="assistant", content=result["result"]["text"])
        await self.memory.append(request.conversation_id, [*request.messages, assistant_message])

        latency_ms = round((time.monotonic() - start) * 1000, 2)
        resolved_model = result["resolved_model"]
        return ChatResponse(
            text=result["result"]["text"],
            provider_id=result["provider_id"],
            requested_model=result["requested_model"],
            resolved_model=resolved_model,
            provider_request_id=result["result"].get("provider_request_id"),
            model=resolved_model,
            usage=ChatUsage.model_validate(result["result"]["usage"]),
            latency_ms=latency_ms,
            conversation_id=request.conversation_id,
            memory_applied=True,
            metadata=request.metadata,
        )

    async def _run_chat(self, request: ChatRequest, *, history: list[ChatMessage]) -> dict[str, Any]:
        provider_id = (request.provider_id or "").strip().lower()
        adapter = self.registry.get(provider_id)
        if adapter is None:
            raise InvalidModelError(f"unsupported provider_id: {provider_id}")

        cfg = self._runtime.provider_defaults.get(provider_id)
        if cfg is None:
            raise InvalidModelError(f"missing provider defaults for: {provider_id}")

        requested_model = (request.model or "").strip() or None
        resolved_model = requested_model or cfg.default_model
        if not resolved_model:
            raise InvalidModelError(f"no model configured for provider_id: {provider_id}")

        combined_messages = [*history, *request.messages]
        provider_call_start = time.monotonic()
        payload = await adapter.chat(
            model=resolved_model,
            messages=combined_messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        provider_latency_s = time.monotonic() - provider_call_start
        self._log_backend_usage(
            provider_id=provider_id,
            resolved_model=resolved_model,
            requested_model=requested_model,
            payload=payload,
            provider_latency_s=provider_latency_s,
        )
        return {
            "provider_id": provider_id,
            "requested_model": requested_model,
            "resolved_model": resolved_model,
            "result": payload,
        }

    def _log_backend_usage(
        self,
        *,
        provider_id: str,
        resolved_model: str,
        requested_model: str | None,
        payload: dict[str, Any],
        provider_latency_s: float,
    ) -> None:
        usage = payload.get("usage") if isinstance(payload, dict) else {}
        usage = usage if isinstance(usage, dict) else {}
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        prompt_tokens = usage.get("prompt_tokens")
        provider_request_id = payload.get("provider_request_id") if isinstance(payload, dict) else None
        completion_tok_per_s: float | None = None
        if (
            isinstance(completion_tokens, int)
            and completion_tokens > 0
            and isinstance(provider_latency_s, (int, float))
            and provider_latency_s > 0
        ):
            completion_tok_per_s = float(completion_tokens) / float(provider_latency_s)
        logger.info(
            "[SHRECKLLM] provider=%s model=%s requested_model=%s request_id=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s provider_latency_s=%.3f completion_tok_per_s=%s",
            provider_id,
            resolved_model,
            requested_model or "<default>",
            provider_request_id,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            provider_latency_s,
            f"{completion_tok_per_s:.2f}" if completion_tok_per_s is not None else "n/a",
        )

        if provider_id != "ollama":
            return
        raw = payload.get("raw") if isinstance(payload, dict) else None
        raw = raw if isinstance(raw, dict) else {}
        eval_count = raw.get("eval_count")
        eval_duration_ns = raw.get("eval_duration")
        prompt_eval_count = raw.get("prompt_eval_count")
        prompt_eval_duration_ns = raw.get("prompt_eval_duration")

        # Ollama reports durations in nanoseconds; derive practical timing values when possible.
        ms_per_token = None
        tok_per_s = None
        if isinstance(eval_count, int) and eval_count > 0 and isinstance(eval_duration_ns, int) and eval_duration_ns > 0:
            eval_duration_ms = eval_duration_ns / 1_000_000.0
            ms_per_token = eval_duration_ms / float(eval_count)
            tok_per_s = float(eval_count) / (eval_duration_ns / 1_000_000_000.0)
        prompt_ms_per_token = None
        if (
            isinstance(prompt_eval_count, int)
            and prompt_eval_count > 0
            and isinstance(prompt_eval_duration_ns, int)
            and prompt_eval_duration_ns > 0
        ):
            prompt_ms_per_token = (prompt_eval_duration_ns / 1_000_000.0) / float(prompt_eval_count)

        logger.info(
            "[SHRECKLLM] ollama_timing model=%s prompt_tokens=%s completion_tokens=%s completion_ms_per_token=%s completion_tok_per_s=%s prompt_ms_per_token=%s",
            resolved_model,
            prompt_eval_count,
            eval_count,
            f"{ms_per_token:.2f}" if ms_per_token is not None else "n/a",
            f"{tok_per_s:.2f}" if tok_per_s is not None else "n/a",
            f"{prompt_ms_per_token:.2f}" if prompt_ms_per_token is not None else "n/a",
        )
