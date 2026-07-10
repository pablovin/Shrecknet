from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any

import httpx
from redis.asyncio import Redis

from app.concurrency import RequestLimiter
from app.config import Settings
from app.config_store import ProviderDefaults, ProviderState, RuntimeConfig, get_runtime_config, reload_runtime_config, update_runtime_config
from app.errors import DependencyUnavailableError, InvalidModelError, ProviderOverloadedError, ProviderTimeoutError
from app.locking import ConversationLockManager
from app.memory import RedisConversationMemory
from app.anthropic_client import AnthropicClient
from app.ollama_client import OllamaClient
from app.openai_client import OpenAIClient
from app.provider_registry import ProviderRegistry
from app.schemas import (
    ChatJobCreateResponse,
    ChatJobStatusResponse,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    ServiceStatusResponse,
)

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


PROVIDER_MODEL_FALLBACKS: dict[str, list[str]] = {
    "openai": ["gpt-5-nano", "gpt-5", "gpt-4o-mini"],
    "anthropic": ["claude-3-haiku-20240307", "claude-opus-4-1-20250805"],
    "ollama_cloud": ["gemma4:31b", "gemma4:31b-cloud"],
}

CLOUD_API_KEY_PROVIDERS = {"openai", "anthropic", "ollama_cloud"}


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
        self._job_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max(1, int(self._runtime.chat_job_queue_max_size)))
        self._chat_jobs: dict[str, dict[str, Any]] = {}
        self._job_events: dict[str, asyncio.Event] = {}
        self._job_worker_task: asyncio.Task[Any] | None = None
        self._job_gc_task: asyncio.Task[Any] | None = None

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
            self._ollama = self._build_provider_adapter("ollama", ollama_cfg)
            self.registry.register(self._ollama)
        else:
            self._ollama = None

        ollama_cloud_cfg = provider_defaults.get("ollama_cloud")
        if ollama_cloud_cfg is not None:
            self._ollama_cloud = self._build_provider_adapter("ollama_cloud", ollama_cloud_cfg)
            self.registry.register(self._ollama_cloud)
        else:
            self._ollama_cloud = None

        openai_cfg = provider_defaults.get("openai")
        if openai_cfg is not None:
            self._openai = self._build_provider_adapter("openai", openai_cfg)
            self.registry.register(self._openai)
        else:
            self._openai = None

        anthropic_cfg = provider_defaults.get("anthropic")
        if anthropic_cfg is not None:
            self._anthropic = self._build_provider_adapter("anthropic", anthropic_cfg)
            self.registry.register(self._anthropic)
        else:
            self._anthropic = None

    def _build_provider_adapter(self, provider_id: str, cfg: ProviderDefaults) -> Any:
        provider_key = provider_id.strip().lower()
        if provider_key == "ollama":
            return OllamaClient(
                base_url=cfg.base_url or "http://localhost:11434",
                timeout_s=self._runtime.request_timeout_seconds,
                keep_alive=self.settings.ollama_keep_alive,
                api_key=cfg.api_key,
            )
        if provider_key == "ollama_cloud":
            return OllamaClient(
                base_url=cfg.base_url or "https://ollama.com",
                timeout_s=self._runtime.request_timeout_seconds,
                keep_alive=None,
                provider_id="ollama_cloud",
                api_key=cfg.api_key,
            )
        if provider_key == "openai":
            return OpenAIClient(
                api_key=cfg.api_key or "",
                timeout_s=self._runtime.request_timeout_seconds,
                base_url=cfg.base_url,
            )
        if provider_key == "anthropic":
            return AnthropicClient(
                api_key=cfg.api_key or "",
                timeout_s=self._runtime.request_timeout_seconds,
                base_url=cfg.base_url,
            )
        raise DependencyUnavailableError(f"provider_not_bound:{provider_key}")

    async def aclose(self) -> None:
        if self._job_worker_task is not None:
            self._job_worker_task.cancel()
        if self._job_gc_task is not None:
            self._job_gc_task.cancel()
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
        await asyncio.gather(
            *(task for task in [self._job_worker_task, self._job_gc_task] if task is not None),
            return_exceptions=True,
        )

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
        self.ensure_background_tasks()
        return self._runtime

    def ensure_background_tasks(self) -> None:
        if self._job_worker_task is None or self._job_worker_task.done():
            self._job_worker_task = asyncio.create_task(self._job_worker_loop())
            logger.info("chat_job_worker_started")
        if self._job_gc_task is None or self._job_gc_task.done():
            self._job_gc_task = asyncio.create_task(self._job_gc_loop())
            logger.info("chat_job_gc_started")

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

    def runtime_config_public_view(self) -> dict[str, Any]:
        payload = self._runtime.model_dump()
        payload.pop("provider_defaults", None)
        payload.pop("provider_states", None)
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

    @staticmethod
    def normalize_provider_models(models: list[str]) -> list[str]:
        normalized: list[str] = []
        for model in models:
            if not isinstance(model, str):
                continue
            cleaned = model.strip()
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    @staticmethod
    def _model_is_discovered(provider_id: str, model: str, discovered_models: list[str]) -> bool:
        discovered_set = set(discovered_models)
        if provider_id == "ollama_cloud":
            return any(variant in discovered_set for variant in _ollama_cloud_model_variants(model))
        return model in discovered_set

    async def provider_model_catalog(
        self,
        provider_id: str,
        cfg: ProviderDefaults | None = None,
    ) -> dict[str, Any]:
        provider_key = provider_id.strip().lower()
        provider_cfg = cfg or self._runtime.provider_defaults.get(provider_key)
        if provider_cfg is None:
            raise InvalidModelError(f"provider not found: {provider_key}")

        adapter = self.registry.get(provider_key) if cfg is None else None
        owns_adapter = False
        if adapter is None:
            adapter = self._build_provider_adapter(provider_key, provider_cfg)
            owns_adapter = True

        try:
            discovered_models = await adapter.list_models()
        except Exception as exc:
            raise DependencyUnavailableError(f"provider_model_catalog_unavailable:{provider_key}") from exc
        finally:
            if owns_adapter and hasattr(adapter, "aclose"):
                await adapter.aclose()

        configured_models = self.normalize_provider_models(list(provider_cfg.models))
        if not discovered_models:
            discovered_models = self.normalize_provider_models(PROVIDER_MODEL_FALLBACKS.get(provider_key, []))
        merged_models: list[str] = []
        for model in [*configured_models, *discovered_models]:
            if isinstance(model, str):
                cleaned = model.strip()
                if cleaned and cleaned not in merged_models:
                    merged_models.append(cleaned)

        return {
            "provider_id": provider_key,
            "configured_models": configured_models,
            "discovered_models": discovered_models,
            "models": merged_models,
        }

    async def validate_provider_models(
        self,
        provider_id: str,
        cfg: ProviderDefaults,
    ) -> dict[str, Any]:
        provider_key = provider_id.strip().lower()
        configured_models = self.normalize_provider_models(list(cfg.models))
        if not configured_models:
            return {
                "valid": False,
                "error": "provider_requires_model",
                "provider_id": provider_key,
                "configured_models": [],
                "discovered_models": [],
                "invalid_models": [],
            }

        catalog = await self.provider_model_catalog(provider_key, cfg)
        discovered_models = catalog["discovered_models"] if isinstance(catalog.get("discovered_models"), list) else []
        invalid_models = [
            model
            for model in configured_models
            if not self._model_is_discovered(provider_key, model, discovered_models)
        ]
        return {
            "valid": not invalid_models,
            "error": None if not invalid_models else "invalid_provider_models",
            "provider_id": provider_key,
            "configured_models": configured_models,
            "discovered_models": discovered_models,
            "invalid_models": invalid_models,
        }

    @staticmethod
    def _provider_activation_blocker(provider_id: str, cfg: ProviderDefaults) -> str | None:
        provider_key = provider_id.strip().lower()
        if provider_key in CLOUD_API_KEY_PROVIDERS and not (cfg.api_key or "").strip():
            return "missing_api_key"
        if cfg.kind == "local" and not (cfg.base_url or "").strip():
            return "missing_base_url"
        return None

    def _effective_provider_active(
        self,
        provider_id: str,
        cfg: ProviderDefaults,
        state: ProviderState,
    ) -> tuple[bool, str | None]:
        blocker = self._provider_activation_blocker(provider_id, cfg)
        if blocker is not None:
            return False, blocker
        if not state.active:
            return False, state.last_validation_error or "provider_not_active"
        return True, None

    async def provider_validation_status(self, provider_id: str) -> dict[str, Any]:
        provider_key = provider_id.strip().lower()
        cfg = self._runtime.provider_defaults.get(provider_key)
        state = self._runtime.provider_states.get(provider_key, ProviderState())
        if cfg is None:
            return {
                "provider_id": provider_key,
                "active": False,
                "reason": "provider_not_configured",
            }

        effective_active, inactive_reason = self._effective_provider_active(provider_key, cfg, state)
        model_statuses = [
            {
                "model": model,
                "available": effective_active,
                "reason": None if effective_active else inactive_reason,
            }
            for model in cfg.models
        ]

        return {
            "provider_id": provider_key,
            "kind": cfg.kind,
            "auth_strategy": cfg.auth_strategy,
            "base_url": cfg.base_url,
            "api_key_present": bool((cfg.api_key or "").strip()),
            "active": effective_active,
            "last_validated_at": state.last_validated_at,
            "last_validation_checked_at": state.last_validation_checked_at,
            "last_validation_failed_at": state.last_validation_failed_at,
            "last_validation_error": state.last_validation_error,
            "last_warmed_at": state.last_warmed_at,
            "last_error": state.last_error,
            "reason": None if effective_active else inactive_reason,
            "models": model_statuses,
        }

    async def live_provider_validation_status(self, provider_id: str) -> dict[str, Any]:
        provider_key = provider_id.strip().lower()
        cfg = self._runtime.provider_defaults.get(provider_key)
        state = self._runtime.provider_states.get(provider_key, ProviderState())
        if cfg is None:
            return {
                "provider_id": provider_key,
                "active": False,
                "reason": "provider_not_configured",
            }

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
            requires_api_key = provider_key in CLOUD_API_KEY_PROVIDERS or cfg.auth_strategy == "api_key"
            api_key_present = bool((cfg.api_key or "").strip()) if requires_api_key else True
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
                    "available": available,
                    "reason": None if available else "model_unavailable",
                }
            )

        active = bool(reachable is not False and auth_valid is not False)
        if active and model_statuses and not all(m["available"] for m in model_statuses):
            active = False
            reason = reason or "model_unavailable"

        return {
            "provider_id": provider_key,
            "kind": cfg.kind,
            "auth_strategy": cfg.auth_strategy,
            "base_url": cfg.base_url,
            "api_key_present": bool((cfg.api_key or "").strip()),
            "active": state.active,
            "last_validated_at": state.last_validated_at,
            "last_validation_checked_at": state.last_validation_checked_at,
            "last_validation_failed_at": state.last_validation_failed_at,
            "last_validation_error": state.last_validation_error,
            "last_warmed_at": state.last_warmed_at,
            "last_error": state.last_error,
            "reachable": reachable,
            "auth_configured": bool((cfg.api_key or "").strip()) if cfg.auth_strategy == "api_key" else True,
            "auth_valid": auth_valid,
            "validation_passed": active,
            "reason": reason,
            "models": model_statuses,
        }

    async def _persist_provider_validation(self, provider_id: str, validation: dict[str, Any]) -> RuntimeConfig:
        provider_key = provider_id.strip().lower()
        checked_at = _utc_now()
        active = self._validation_succeeded(validation)
        error = None if active else str(validation.get("reason") or validation.get("error") or "provider validation failed")
        states = dict(self._runtime.provider_states)
        previous = states.get(provider_key, ProviderState())
        states[provider_key] = ProviderState(
            active=active,
            last_validated_at=checked_at if active else previous.last_validated_at,
            last_validation_checked_at=checked_at,
            last_validation_failed_at=None if active else checked_at,
            last_validation_error=error,
            last_warmed_at=previous.last_warmed_at,
            last_error=None if active else error,
        )
        update_runtime_config({"provider_states": states})
        reload_runtime_config()
        return await self.refresh_runtime()

    @staticmethod
    def _validation_succeeded(validation: dict[str, Any]) -> bool:
        if "validation_passed" in validation:
            return validation.get("validation_passed") is True
        if "active" in validation:
            return validation.get("active") is True
        return validation.get("valid") is True

    async def test_provider_functionality(self, provider_id: str, *, ping: bool = True) -> dict[str, Any]:
        provider_key = provider_id.strip().lower()
        if provider_key not in self._runtime.provider_defaults:
            raise InvalidModelError(f"provider not found: {provider_key}")

        logger.info(
            "[SHRECKLLM_PROVIDER_EVAL] step=start provider=%s ping=%s",
            provider_key,
            ping,
        )
        validation = await self.live_provider_validation_status(provider_key)
        logger.info(
            "[SHRECKLLM_PROVIDER_EVAL] step=validation provider=%s passed=%s reason=%s reachable=%s auth_valid=%s models=%s",
            provider_key,
            self._validation_succeeded(validation),
            validation.get("reason"),
            validation.get("reachable"),
            validation.get("auth_valid"),
            validation.get("models"),
        )
        if self._validation_succeeded(validation) and ping:
            adapter = self.registry.get(provider_key)
            cfg = self._runtime.provider_defaults.get(provider_key)
            ping_model = str((cfg.models or [""])[0]).strip() if cfg is not None else ""
            validation["test_model"] = ping_model or None
            if adapter is None:
                validation["validation_passed"] = False
                validation["reason"] = "provider_not_bound"
            elif not ping_model:
                validation["validation_passed"] = False
                validation["reason"] = "missing_test_model"
            else:
                try:
                    logger.info(
                        "[SHRECKLLM_PROVIDER_EVAL] step=functional_ping_start provider=%s model=%s",
                        provider_key,
                        ping_model,
                    )
                    await adapter.chat(
                        model=ping_model,
                        messages=[ChatMessage(role="user", content="ping")],
                        temperature=0.0,
                    )
                    validation["functional_test_passed"] = True
                    logger.info(
                        "[SHRECKLLM_PROVIDER_EVAL] step=functional_ping_done provider=%s model=%s",
                        provider_key,
                        ping_model,
                    )
                except Exception as exc:
                    validation["validation_passed"] = False
                    validation["functional_test_passed"] = False
                    validation["reason"] = str(exc)
                    logger.warning(
                        "[SHRECKLLM_PROVIDER_EVAL] step=functional_ping_failed provider=%s model=%s error=%s",
                        provider_key,
                        ping_model,
                        exc,
                    )

        await self._persist_provider_validation(provider_key, validation)
        provider_payload = await self.provider_validation_status(provider_key)
        aggregate = await self.all_provider_validation_statuses()
        logger.info(
            "[SHRECKLLM_PROVIDER_EVAL] step=done provider=%s active=%s reason=%s operational_provider_ids=%s",
            provider_key,
            provider_payload.get("active"),
            provider_payload.get("reason"),
            aggregate["operational_provider_ids"],
        )
        return {
            "provider": provider_payload,
            "shreckllm_operational": aggregate["shreckllm_operational"],
            "operational_provider_ids": aggregate["operational_provider_ids"],
        }

    async def revalidate_all_providers(self) -> dict[str, Any]:
        logger.info(
            "[SHRECKLLM_PROVIDER_EVAL] step=revalidate_all_start providers=%s",
            sorted(self._runtime.provider_defaults.keys()),
        )
        results: dict[str, Any] = {}
        for provider_id in sorted(self._runtime.provider_defaults.keys()):
            try:
                results[provider_id] = await self.test_provider_functionality(provider_id, ping=True)
            except Exception as exc:
                await self._persist_provider_validation(
                    provider_id,
                    {
                        "provider_id": provider_id,
                        "active": False,
                        "reason": str(exc),
                    },
                )
                results[provider_id] = await self.provider_validation_status(provider_id)
        aggregate = await self.all_provider_validation_statuses()
        logger.info(
            "[SHRECKLLM_PROVIDER_EVAL] step=revalidate_all_done shreckllm_operational=%s operational_provider_ids=%s",
            aggregate["shreckllm_operational"],
            aggregate["operational_provider_ids"],
        )
        return {**aggregate, "results": results}

    async def refresh_runtime_and_validate(self, provider_ids: list[str] | None = None, *, ping: bool = True) -> dict[str, Any]:
        await self.refresh_runtime()
        if provider_ids is None:
            return await self.revalidate_all_providers()
        results: dict[str, Any] = {}
        for provider_id in sorted(set(provider_ids)):
            if provider_id in self._runtime.provider_defaults:
                results[provider_id] = await self.test_provider_functionality(provider_id, ping=ping)
        aggregate = await self.all_provider_validation_statuses()
        return {**aggregate, "results": results}

    async def all_provider_validation_statuses(self) -> dict[str, Any]:
        providers: dict[str, Any] = {}
        for provider_id in sorted(self._runtime.provider_defaults.keys()):
            providers[provider_id] = await self.provider_validation_status(provider_id)
        operational_provider_ids = self._operational_provider_ids(providers)
        return {
            "shreckllm_operational": bool(operational_provider_ids),
            "operational_provider_ids": operational_provider_ids,
            "providers": providers,
        }

    @staticmethod
    def provider_summary_from_payload(providers: dict[str, Any], operational_provider_ids: list[str]) -> dict[str, Any]:
        provider_ids = sorted(str(provider_id) for provider_id in providers.keys())
        active_provider_ids = sorted(str(provider_id) for provider_id in operational_provider_ids)
        return {
            "total": len(provider_ids),
            "active": len(active_provider_ids),
            "inactive": max(0, len(provider_ids) - len(active_provider_ids)),
            "provider_ids": provider_ids,
            "active_provider_ids": active_provider_ids,
        }

    @staticmethod
    def _provider_has_usable_model(provider_payload: dict[str, Any]) -> bool:
        models = provider_payload.get("models")
        if not isinstance(models, list):
            return False
        return any(isinstance(model, dict) and model.get("available") is True for model in models)

    @classmethod
    def _operational_provider_ids(cls, providers: dict[str, Any]) -> list[str]:
        operational_provider_ids: list[str] = []
        for provider_id, payload in sorted(providers.items()):
            if not isinstance(payload, dict):
                continue
            if payload.get("active") is True and cls._provider_has_usable_model(payload):
                operational_provider_ids.append(str(provider_id))
        return operational_provider_ids

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
            configured_models: list[str] = []
            cfg = self._runtime.provider_defaults.get(provider_id)
            if cfg is not None:
                configured_models = list(cfg.models)
                try:
                    discovered = set(await adapter.list_models())
                    model_ok = any(model in discovered for model in configured_models) if discovered else bool(configured_models)
                except Exception:
                    model_ok = False
            dependencies[provider_id] = {
                "ok": bool(ok),
                "configured_models": configured_models,
                "any_configured_model_available": model_ok,
            }
            provider_ready = provider_ready or (bool(ok) and bool(model_ok))

        return {
            "ready": redis_ok and provider_ready,
            "dependencies": dependencies,
        }

    async def models(self) -> dict[str, Any]:
        providers_payload: dict[str, Any] = {}
        for provider_id in self.registry.provider_ids():
            cfg = self._runtime.provider_defaults.get(provider_id)
            state = self._runtime.provider_states.get(provider_id, ProviderState())
            if cfg is None:
                continue
            effective_active, _ = self._effective_provider_active(provider_id, cfg, state)
            if not effective_active:
                continue
            try:
                catalog = await self.provider_model_catalog(provider_id)
            except Exception:
                catalog = {
                    "configured_models": self.normalize_provider_models(list(cfg.models)) if cfg else [],
                    "discovered_models": [],
                    "models": self.normalize_provider_models(list(cfg.models)) if cfg else [],
                }
            providers_payload[provider_id] = catalog

        return {
            "providers": providers_payload,
        }

    async def status(self) -> ServiceStatusResponse:
        ready_payload = await self.ready()
        validation_payload = await self.all_provider_validation_statuses()
        operational_provider_ids = validation_payload.get("operational_provider_ids")
        if not isinstance(operational_provider_ids, list):
            operational_provider_ids = []
        providers = validation_payload.get("providers")
        if not isinstance(providers, dict):
            providers = {}
        return ServiceStatusResponse(
            shreckllm_operational=validation_payload.get("shreckllm_operational") is True,
            operational_provider_ids=[str(provider_id) for provider_id in operational_provider_ids],
            providers_summary=self.provider_summary_from_payload(
                providers,
                [str(provider_id) for provider_id in operational_provider_ids],
            ),
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

    async def _set_provider_state(
        self,
        provider_id: str,
        *,
        active: bool,
        last_validated_at: str | None = None,
        last_warmed_at: str | None = None,
        last_error: str | None = None,
    ) -> RuntimeConfig:
        provider_key = provider_id.strip().lower()
        states = dict(self._runtime.provider_states)
        previous = states.get(provider_key, ProviderState())
        states[provider_key] = ProviderState(
            active=active,
            last_validated_at=last_validated_at if last_validated_at is not None else previous.last_validated_at,
            last_validation_checked_at=previous.last_validation_checked_at,
            last_validation_failed_at=previous.last_validation_failed_at,
            last_validation_error=previous.last_validation_error,
            last_warmed_at=last_warmed_at if last_warmed_at is not None else previous.last_warmed_at,
            last_error=last_error,
        )
        update_runtime_config({"provider_states": states})
        reload_runtime_config()
        return await self.refresh_runtime()

    async def activate_provider(self, provider_id: str) -> dict[str, Any]:
        provider_key = provider_id.strip().lower()
        cfg = self._runtime.provider_defaults.get(provider_key)
        if cfg is None:
            raise InvalidModelError(f"provider not found: {provider_key}")

        validation = await self.live_provider_validation_status(provider_key)
        validated_at = _utc_now()
        if not self._validation_succeeded(validation):
            error = str(validation.get("reason") or validation.get("error") or "provider validation failed")
            await self._persist_provider_validation(provider_key, validation)
            await self._set_provider_state(
                provider_key,
                active=False,
                last_validated_at=validated_at,
                last_error=error,
            )
            raise DependencyUnavailableError(error)

        adapter = self.registry.get(provider_key)
        if adapter is None:
            error = f"provider_not_bound:{provider_key}"
            await self._persist_provider_validation(provider_key, {"provider_id": provider_key, "active": False, "reason": error})
            await self._set_provider_state(
                provider_key,
                active=False,
                last_validated_at=validated_at,
                last_error=error,
            )
            raise DependencyUnavailableError(error)

        try:
            ping_model = str((cfg.models or [""])[0]).strip()
            if not ping_model:
                raise DependencyUnavailableError("missing_activation_model")
            await adapter.chat(
                model=ping_model,
                messages=[ChatMessage(role="user", content="ping")],
                temperature=0.0,
            )
        except Exception as exc:
            await self._persist_provider_validation(provider_key, {"provider_id": provider_key, "active": False, "reason": str(exc)})
            await self._set_provider_state(
                provider_key,
                active=False,
                last_validated_at=validated_at,
                last_error=str(exc),
            )
            raise DependencyUnavailableError(str(exc)) from exc

        await self._persist_provider_validation(provider_key, {**validation, "active": True, "reason": None})
        await self._set_provider_state(
            provider_key,
            active=True,
            last_validated_at=validated_at,
            last_warmed_at=validated_at,
            last_error=None,
        )
        return await self.provider_validation_status(provider_key)

    async def deactivate_provider(self, provider_id: str) -> dict[str, Any]:
        provider_key = provider_id.strip().lower()
        if provider_key not in self._runtime.provider_defaults:
            raise InvalidModelError(f"provider not found: {provider_key}")
        await self._set_provider_state(provider_key, active=False, last_error=None)
        return await self.provider_validation_status(provider_key)

    async def prewarm_active_providers(self) -> None:
        active_provider_ids: list[str] = []
        for provider_id, state in self._runtime.provider_states.items():
            cfg = self._runtime.provider_defaults.get(provider_id)
            if cfg is None:
                continue
            effective_active, _ = self._effective_provider_active(provider_id, cfg, state)
            if effective_active:
                active_provider_ids.append(provider_id)
        if not active_provider_ids:
            logger.info("[SHRECKLLM_PREWARM] step=skipped reason=no_active_providers")
            return
        logger.info("[SHRECKLLM_PREWARM] step=start active_provider_ids=%s", active_provider_ids)
        for provider_id in active_provider_ids:
            adapter = self.registry.get(provider_id)
            cfg = self._runtime.provider_defaults.get(provider_id)
            if adapter is None:
                await self._set_provider_state(provider_id, active=False, last_error="provider_not_bound")
                logger.info("[SHRECKLLM_PREWARM] step=skipped provider=%s reason=provider_not_bound", provider_id)
                continue
            model = str((cfg.models or [""])[0] if cfg else "").strip()
            if not model:
                await self._set_provider_state(provider_id, active=False, last_error="missing_prewarm_model")
                logger.info("[SHRECKLLM_PREWARM] step=skipped provider=%s reason=missing_prewarm_model", provider_id)
                continue
            logger.info("[SHRECKLLM_PREWARM] step=provider_start provider=%s model=%s", provider_id, model)
            try:
                await adapter.chat(
                    model=model,
                    messages=[ChatMessage(role="user", content="ping")],
                    temperature=0.0,
                )
                await self._set_provider_state(
                    provider_id,
                    active=True,
                    last_warmed_at=_utc_now(),
                    last_error=None,
                )
                logger.info("[SHRECKLLM_PREWARM] step=provider_done provider=%s model=%s", provider_id, model)
            except Exception as exc:
                await self._set_provider_state(provider_id, active=False, last_error=str(exc))
                logger.warning("[SHRECKLLM_PREWARM] step=provider_failed provider=%s model=%s error=%s", provider_id, model, exc)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        job = await self.submit_chat_job(request)
        return await self.wait_for_chat_job_result(job.job_id, timeout_s=self._runtime.request_timeout_seconds)

    async def submit_chat_job(self, request: ChatRequest) -> ChatJobCreateResponse:
        self.ensure_background_tasks()
        if self._job_queue.full():
            raise ProviderOverloadedError("chat job queue full")
        provider_key = (request.provider_id or "").strip().lower()
        cfg = self._runtime.provider_defaults.get(provider_key)
        state = self._runtime.provider_states.get(provider_key, ProviderState())
        if cfg is None:
            raise DependencyUnavailableError(f"LLM provider {provider_key} is not configured")
        effective_active, reason = self._effective_provider_active(provider_key, cfg, state)
        if not effective_active:
            reason = reason or "provider_not_active"
            raise DependencyUnavailableError(f"LLM provider {provider_key} is not active: {reason}")
        now = time.time()
        job_id = str(uuid4())
        self._chat_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "expires_at": None,
            "provider_id": request.provider_id,
            "resolved_model": None,
            "requested_model": request.model,
            "retry_count": 0,
            "error": None,
            "request": request,
            "response": None,
        }
        self._job_events[job_id] = asyncio.Event()
        self._job_queue.put_nowait(job_id)
        logger.info(
            "chat_job_queued job_id=%s provider=%s model=%s queue_depth=%s",
            job_id,
            request.provider_id,
            request.model,
            self._job_queue.qsize(),
        )
        return ChatJobCreateResponse(job_id=job_id, status="queued", created_at=now, expires_at=None)

    def get_chat_job_status(self, job_id: str) -> ChatJobStatusResponse | None:
        row = self._chat_jobs.get(job_id)
        if row is None:
            return None
        return ChatJobStatusResponse(
            job_id=job_id,
            status=str(row.get("status") or "unknown"),
            created_at=float(row.get("created_at") or 0.0),
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            expires_at=row.get("expires_at"),
            provider_id=row.get("provider_id"),
            resolved_model=row.get("resolved_model"),
            requested_model=row.get("requested_model"),
            retry_count=int(row.get("retry_count") or 0),
            error=row.get("error"),
        )

    async def wait_for_chat_job_result(self, job_id: str, *, timeout_s: float) -> ChatResponse:
        event = self._job_events.get(job_id)
        if event is None:
            raise InvalidModelError("job not found")
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.01, float(timeout_s)))
        except asyncio.TimeoutError as exc:
            raise ProviderTimeoutError(f"chat job timeout job_id={job_id}") from exc
        row = self._chat_jobs.get(job_id)
        if not row:
            raise InvalidModelError("job expired")
        status = str(row.get("status") or "")
        if status == "succeeded" and isinstance(row.get("response"), ChatResponse):
            return row["response"]
        if status == "failed":
            raise DependencyUnavailableError(str(row.get("error") or "chat job failed"))
        raise ProviderTimeoutError(f"chat job unfinished status={status}")

    async def get_chat_job_result(self, job_id: str) -> ChatResponse | None:
        row = self._chat_jobs.get(job_id)
        if row is None:
            return None
        return row.get("response") if isinstance(row.get("response"), ChatResponse) else None

    async def _job_worker_loop(self) -> None:
        while True:
            job_id = await self._job_queue.get()
            row = self._chat_jobs.get(job_id)
            if row is None:
                continue
            row["status"] = "running"
            row["started_at"] = time.time()
            request = row.get("request")
            logger.info(
                "chat_job_started job_id=%s provider=%s model=%s queue_depth=%s",
                job_id,
                getattr(request, "provider_id", None),
                getattr(request, "model", None),
                self._job_queue.qsize(),
            )
            try:
                response = await self._execute_chat_request(request)
                row["response"] = response
                row["status"] = "succeeded"
                row["provider_id"] = response.provider_id
                row["resolved_model"] = response.resolved_model
                row["requested_model"] = response.requested_model
            except Exception as exc:
                row["status"] = "failed"
                row["error"] = str(exc)
            finally:
                row["finished_at"] = time.time()
                row["expires_at"] = row["finished_at"] + max(1, int(self._runtime.chat_job_result_ttl_seconds))
                logger.info(
                    "chat_job_finished job_id=%s status=%s provider=%s model=%s elapsed_ms=%s error=%s",
                    job_id,
                    row.get("status"),
                    row.get("provider_id") or getattr(request, "provider_id", None),
                    row.get("resolved_model") or getattr(request, "model", None),
                    round((float(row["finished_at"]) - float(row["started_at"] or row["finished_at"])) * 1000, 2),
                    row.get("error"),
                )
                event = self._job_events.get(job_id)
                if event is not None:
                    event.set()

    async def _job_gc_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            now = time.time()
            expired = [
                job_id
                for job_id, row in self._chat_jobs.items()
                if isinstance(row.get("expires_at"), (int, float)) and float(row["expires_at"]) <= now
            ]
            for job_id in expired:
                self._chat_jobs.pop(job_id, None)
                self._job_events.pop(job_id, None)

    async def _execute_chat_request(self, request: ChatRequest) -> ChatResponse:
        start = time.monotonic()
        attempts = max(1, int(self._runtime.chat_job_max_retries) + 1)
        for attempt in range(1, attempts + 1):
            try:
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
            except (ProviderOverloadedError, ProviderTimeoutError, DependencyUnavailableError) as exc:
                if attempt >= attempts:
                    raise
                await asyncio.sleep(min(10.0, (2 ** attempt) + random.uniform(0.1, 0.8)))

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
        resolved_model = requested_model
        if not resolved_model:
            raise InvalidModelError(f"model is required for provider_id: {provider_id}")

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
