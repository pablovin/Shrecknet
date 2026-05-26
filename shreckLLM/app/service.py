from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from redis.asyncio import Redis

from app.concurrency import RequestLimiter
from app.config import Settings
from app.config_store import RuntimeConfig, get_runtime_config
from app.errors import DependencyUnavailableError, InvalidModelError, ProviderOverloadedError
from app.locking import ConversationLockManager
from app.memory import RedisConversationMemory
from app.anthropic_client import AnthropicClient
from app.ollama_client import OllamaClient
from app.openai_client import OpenAIClient
from app.provider_registry import ProviderRegistry
from app.schemas import ChatMessage, ChatRequest, ChatResponse, ChatUsage, ServiceStatusResponse

logger = logging.getLogger(__name__)


def _ollama_cloud_model_variants(model: str) -> set[str]:
    cleaned = model.strip()
    if not cleaned:
        return set()
    variants = {cleaned}
    if cleaned.endswith("-cloud"):
        variants.add(cleaned.removesuffix("-cloud"))
    else:
        variants.add(f"{cleaned}-cloud")
    return variants


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
        self._ollama_cloud: OllamaClient | None = None
        self._openai: OpenAIClient | None = None
        self._anthropic: AnthropicClient | None = None
        self._bind_providers()
        self.limiter = RequestLimiter(max_concurrent=self._runtime.max_concurrent_requests)
        self._provider_semaphores: dict[str, asyncio.Semaphore] = {}
        self._provider_waiting: dict[str, int] = {}
        self._provider_rejected: dict[str, int] = {}
        self._provider_cooldown_until: dict[str, float] = {}
        self._provider_lock = asyncio.Lock()
        self._init_provider_limiters()

    def _init_provider_limiters(self) -> None:
        self._provider_semaphores = {}
        self._provider_waiting = {}
        self._provider_rejected = {}
        self._provider_cooldown_until = {}
        for provider_id in self._runtime.provider_defaults.keys():
            limits = (self._runtime.provider_limits or {}).get(provider_id, {})
            max_concurrent = int(limits.get("max_concurrent", 0) or 0)
            if max_concurrent > 0:
                self._provider_semaphores[provider_id] = asyncio.Semaphore(max_concurrent)
                self._provider_waiting[provider_id] = 0
                self._provider_rejected[provider_id] = 0

    def _bind_providers(self) -> None:
        self.registry = ProviderRegistry()
        provider_defaults = self._runtime.provider_defaults

        ollama_cfg = provider_defaults.get("ollama")
        if ollama_cfg is not None:
            self._ollama = OllamaClient(
                base_url=ollama_cfg.base_url or "http://localhost:11434",
                timeout_s=self._runtime.request_timeout_seconds,
                keep_alive=self.settings.ollama_keep_alive,
                api_key=ollama_cfg.api_key,
            )
            self.registry.register(self._ollama)
        else:
            self._ollama = None

        ollama_cloud_cfg = provider_defaults.get("ollama_cloud")
        if ollama_cloud_cfg is not None:
            self._ollama_cloud = OllamaClient(
                base_url=ollama_cloud_cfg.base_url or "https://ollama.com",
                timeout_s=self._runtime.request_timeout_seconds,
                keep_alive=None,
                provider_id="ollama_cloud",
                api_key=ollama_cloud_cfg.api_key,
            )
            self.registry.register(self._ollama_cloud)
        else:
            self._ollama_cloud = None

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
        if self._ollama_cloud is not None:
            closers.append(self._ollama_cloud.aclose())
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
        if self._ollama_cloud is not None:
            closers.append(self._ollama_cloud.aclose())
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
        self._init_provider_limiters()
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
            for provider_cfg in providers.values():
                if isinstance(provider_cfg, dict):
                    provider_cfg["api_key"] = self._mask_secret(provider_cfg.get("api_key"))
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

    async def provider_validation_status(self, provider_id: str) -> dict[str, Any]:
        provider_key = provider_id.strip().lower()
        cfg = self._runtime.provider_defaults.get(provider_key)
        if cfg is None:
            return {"provider_id": provider_key, "configured": False, "valid": False, "reason": "provider_not_configured"}

        adapter = self.registry.get(provider_key)
        discovered_models: list[str] = []
        reachable: bool | None = None
        auth_valid: bool | None = None
        reason: str | None = None

        if cfg.kind == "local":
            base_url = (cfg.base_url or "").rstrip("/")
            path = cfg.healthcheck_path or "/health"
            if not base_url:
                reachable = False
                reason = "missing_base_url"
            else:
                try:
                    async with httpx.AsyncClient(base_url=base_url, timeout=self._runtime.request_timeout_seconds) as client:
                        resp = await client.get(path)
                        reachable = resp.status_code < 500
                except Exception:
                    reachable = False
                if reachable is False:
                    reason = "unreachable"
            auth_valid = True
        else:
            api_key_present = bool((cfg.api_key or "").strip()) if cfg.auth_strategy == "api_key" else True
            if not api_key_present:
                auth_valid = False
                reason = "missing_api_key"
            else:
                auth_valid = True
                if provider_key == "openai" and self._openai is not None:
                    probe = await self._openai.validate_api_key()
                    auth_valid = probe.get("valid")
                    reason = probe.get("error")
                elif provider_key == "anthropic" and self._anthropic is not None:
                    probe = await self._anthropic.validate_api_key()
                    auth_valid = probe.get("valid")
                    reason = probe.get("error")
            reachable = True

        if adapter is not None:
            try:
                discovered_models = await adapter.list_models()
            except Exception:
                discovered_models = []

        configured_models = list(cfg.models)
        model_statuses: list[dict[str, Any]] = []
        discovered_set = set(discovered_models)
        for model in configured_models:
            if not discovered_models:
                available = True
            elif provider_key == "ollama_cloud":
                available = any(variant in discovered_set for variant in _ollama_cloud_model_variants(model))
            else:
                available = model in discovered_set
            model_statuses.append(
                {
                    "model": model,
                    "configured": True,
                    "available": available,
                    "valid": bool(available) and auth_valid is not False and reachable is not False,
                    "reason": None if available else "model_unavailable",
                }
            )

        valid = bool(reachable is not False and auth_valid is not False)
        if valid and model_statuses and not all(m["valid"] for m in model_statuses):
            valid = False
            reason = reason or "model_unavailable"

        return {
            "provider_id": provider_key,
            "kind": cfg.kind,
            "auth_strategy": cfg.auth_strategy,
            "configured": True,
            "reachable": reachable,
            "auth_configured": bool((cfg.api_key or "").strip()) if cfg.auth_strategy == "api_key" else True,
            "auth_valid": auth_valid,
            "valid": valid,
            "reason": reason,
            "default_model": cfg.default_model,
            "models": model_statuses,
        }

    async def all_provider_validation_statuses(self) -> dict[str, Any]:
        providers: dict[str, Any] = {}
        for provider_id in sorted(self._runtime.provider_defaults.keys()):
            providers[provider_id] = await self.provider_validation_status(provider_id)
        return {"providers": providers}

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
            provider_limiters={
                provider_id: {
                    "active_requests": int(
                        ((self._runtime.provider_limits or {}).get(provider_id, {}).get("max_concurrent", 0) or 0)
                    ) - sem._value,
                    "queue_depth": self._provider_waiting.get(provider_id, 0),
                    "cooldown_until": self._provider_cooldown_until.get(provider_id),
                    "rejected_due_to_queue": self._provider_rejected.get(provider_id, 0),
                }
                for provider_id, sem in self._provider_semaphores.items()
            },
        )

    async def prewarm_local_llm(self) -> None:
        if not self.settings.ollama_prewarm_on_startup:
            print("[SHRECKLLM_PREWARM] step=skipped reason=disabled")
            return
        if self._ollama is None:
            print("[SHRECKLLM_PREWARM] step=skipped reason=ollama_provider_not_bound")
            return
        # Intentionally source prewarm target from bootstrap settings (seed/env),
        # not runtime DB config, to keep startup deterministic across deployments.
        bootstrap_ollama = self.settings.bootstrap_provider_defaults.get("ollama", {})
        bootstrap_model = str(bootstrap_ollama.get("default_model") or "").strip()
        if not bootstrap_model:
            print("[SHRECKLLM_PREWARM] step=skipped reason=missing_default_model")
            return
        print(f"[SHRECKLLM_PREWARM] step=start model={bootstrap_model} keep_alive={self.settings.ollama_keep_alive}")
        try:
            await self._ollama.chat(
                model=bootstrap_model,
                messages=[ChatMessage(role="user", content="ping")],
                temperature=0.0,
            )
            print(f"[SHRECKLLM_PREWARM] step=done model={bootstrap_model} keep_alive={self.settings.ollama_keep_alive}")
            logger.info("[SHRECKLLM] ollama_prewarm_done model=%s keep_alive=%s", bootstrap_model, self.settings.ollama_keep_alive)
        except Exception as exc:
            print(f"[SHRECKLLM_PREWARM] step=failed model={bootstrap_model} error={exc}")
            logger.warning("[SHRECKLLM] ollama_prewarm_failed model=%s error=%s", bootstrap_model, exc)

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

        now = time.monotonic()
        cooldown_until = self._provider_cooldown_until.get(provider_id, 0.0)
        if cooldown_until > now:
            raise ProviderOverloadedError(
                f"provider cooldown active provider={provider_id} retry_after={round(cooldown_until - now, 2)}"
            )

        combined_messages = [*history, *request.messages]
        provider_call_start = time.monotonic()
        sem = self._provider_semaphores.get(provider_id)
        if sem is None:
            payload = await adapter.chat(
                model=resolved_model,
                messages=combined_messages,
                temperature=request.temperature,
            )
        else:
            limits = (self._runtime.provider_limits or {}).get(provider_id, {})
            queue_size = int(limits.get("max_queue_size", 0) or 0)
            queue_wait_s = float(limits.get("max_queue_wait_seconds", self._runtime.max_queue_wait_seconds))
            async with self._provider_lock:
                waiting = self._provider_waiting.get(provider_id, 0)
                if queue_size > 0 and waiting >= queue_size:
                    self._provider_rejected[provider_id] = self._provider_rejected.get(provider_id, 0) + 1
                    raise ProviderOverloadedError(f"provider queue full provider={provider_id}")
                self._provider_waiting[provider_id] = waiting + 1
            try:
                await asyncio.wait_for(sem.acquire(), timeout=max(0.01, queue_wait_s))
            except asyncio.TimeoutError as exc:
                self._provider_rejected[provider_id] = self._provider_rejected.get(provider_id, 0) + 1
                raise ProviderOverloadedError(f"provider queue wait timeout provider={provider_id}") from exc
            finally:
                async with self._provider_lock:
                    self._provider_waiting[provider_id] = max(0, self._provider_waiting.get(provider_id, 1) - 1)
            try:
                payload = await adapter.chat(
                    model=resolved_model,
                    messages=combined_messages,
                    temperature=request.temperature,
                )
            except ProviderOverloadedError:
                cooldown_s = float(limits.get("cooldown_seconds_on_429", 10.0) or 10.0)
                self._provider_cooldown_until[provider_id] = time.monotonic() + max(1.0, cooldown_s)
                raise
            finally:
                sem.release()
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
