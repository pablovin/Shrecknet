"""shreckLLM HTTP client with OpenAIClient-compatible surface."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections import defaultdict
from typing import Any

import httpx

from app.core.config_store import LLMModelTarget

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


class ShreckLLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 60.0,
        max_retries: int = 2,
        poll_without_deadline: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.poll_without_deadline = bool(poll_without_deadline)
        self._max_backoff_s = 20.0
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)
        self._usage_events: list[dict[str, Any]] = []
        self._validated_providers: set[str] = set()

    async def aclose(self) -> None:
        await self._http.aclose()
        self._usage_events.clear()
        self._validated_providers.clear()

    async def __aenter__(self) -> "ShreckLLMClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        await self.aclose()

    async def chat(
        self,
        model: str | LLMModelTarget,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        conversation_id: str | None = None,
        use_conversation_memory: bool = False,
        usage_tag: str | None = None,
        return_metadata: bool = False,
        max_tokens: int | None = None,
    ) -> str | dict[str, Any]:
        target = self._coerce_target(model)
        await self.ensure_provider_ready(target.provider)
        payload: dict[str, Any] = {
            "provider_id": target.provider,
            "model": target.name,
            "messages": messages,
            "temperature": temperature,
            "conversation_id": conversation_id,
            "use_conversation_memory": use_conversation_memory,
            "metadata": {"usage_tag": usage_tag} if usage_tag else None,
            "max_tokens": max_tokens,
        }
        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                job_id = await self.submit_chat_job(payload)
                logger.info(
                    "shreckllm_chat_job_submitted provider=%s model=%s job_id=%s usage_tag=%s attempt=%s",
                    target.provider,
                    target.name,
                    job_id,
                    usage_tag,
                    attempt,
                )
                data = await self.wait_for_chat_job(
                    job_id,
                    timeout_s=None if self.poll_without_deadline else self.timeout,
                    poll_interval_s=1.0,
                )
                text = str(data.get("text") or "")
                usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                resolved_model = str(data.get("resolved_model") or target.name)
                provider_id = str(data.get("provider_id") or target.provider)
                self._record_usage_event(
                    model=f"{provider_id}:{resolved_model}",
                    usage=usage,
                    usage_tag=usage_tag,
                )
                if return_metadata:
                    return {
                        "text": text,
                        "usage": usage,
                        "response_metadata": {
                            "provider_id": provider_id,
                            "resolved_model": resolved_model,
                            "provider_request_id": data.get("provider_request_id"),
                        },
                    }
                return text
            except Exception as exc:
                retry_after_s: float | None = None
                if isinstance(exc, httpx.HTTPStatusError):
                    try:
                        error_payload = exc.response.json()
                        detail = error_payload.get("detail") if isinstance(error_payload, dict) else None
                        if isinstance(detail, dict):
                            value = detail.get("retry_after_seconds")
                            if isinstance(value, (int, float)):
                                retry_after_s = float(value)
                    except Exception:
                        retry_after_s = None
                if not self._is_retryable_exception(exc) or attempt >= attempts:
                    error_text = str(exc).strip()
                    if not error_text:
                        error_text = "<empty_message>"
                    logger.error(
                        "shreckllm chat failed provider=%s model=%s error_type=%s error=%s",
                        target.provider,
                        target.name,
                        type(exc).__name__,
                        error_text,
                        exc_info=True,
                    )
                    raise
                base_sleep = min(self._max_backoff_s, (2 ** attempt) + random.uniform(0.25, 1.25))
                await asyncio.sleep(max(base_sleep, retry_after_s or 0.0))

        raise RuntimeError("Unreachable retry loop state in ShreckLLMClient.chat")

    def _headers(self) -> dict[str, str]:
        token = str(os.getenv("SHRECKLLM_INTERNAL_SERVICE_TOKEN") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def get_provider_statuses(self) -> dict[str, Any]:
        """Read ShreckLLM's cached provider state without running a functional ping."""
        response = await self._http.get("/providers", headers=self._headers())
        response.raise_for_status()
        data = response.json() if response.content else {}
        return data if isinstance(data, dict) else {}

    async def ensure_provider_ready(self, provider_id: str) -> None:
        provider_key = provider_id.strip().lower()
        if provider_key in self._validated_providers:
            return
        try:
            payload = await self.get_provider_statuses()
        except Exception as exc:
            raise RuntimeError(f"LLM provider {provider_key} failed validation: {exc}") from exc
        providers = payload.get("providers") if isinstance(payload, dict) else None
        provider = providers.get(provider_key) if isinstance(providers, dict) else None
        active = bool(provider.get("active", provider.get("valid"))) if isinstance(provider, dict) else False
        if not active:
            reason = None
            if isinstance(provider, dict):
                reason = provider.get("last_validation_error") or provider.get("reason") or provider.get("last_error")
            raise RuntimeError(f"LLM provider {provider_key} failed validation: {reason or 'provider validation failed'}")
        self._validated_providers.add(provider_key)

    async def submit_chat_job(self, payload: dict[str, Any]) -> str:
        response = await self._http.post("/chat/jobs", json=payload)
        response.raise_for_status()
        data = response.json() if response.content else {}
        job_id = str(data.get("job_id") or "").strip()
        if not job_id:
            raise RuntimeError("missing chat job_id from shreckLLM")
        return job_id

    async def wait_for_chat_job(
        self,
        job_id: str,
        *,
        timeout_s: float | None,
        poll_interval_s: float,
    ) -> dict[str, Any]:
        start = asyncio.get_running_loop().time()
        interval = max(0.05, float(poll_interval_s))
        last_status = ""
        while True:
            status_resp = await self._http.get(f"/chat/jobs/{job_id}")
            status_resp.raise_for_status()
            status_data = status_resp.json() if status_resp.content else {}
            status_value = str((status_data or {}).get("status") or "").strip().lower()
            if status_value != last_status:
                logger.info(
                    "shreckllm_chat_job_status job_id=%s status=%s provider=%s model=%s retry_count=%s",
                    job_id,
                    status_value,
                    (status_data or {}).get("provider_id"),
                    (status_data or {}).get("resolved_model") or (status_data or {}).get("requested_model"),
                    (status_data or {}).get("retry_count"),
                )
                last_status = status_value
            if status_value == "succeeded":
                result = await self._http.get(f"/chat/jobs/{job_id}/result")
                result.raise_for_status()
                data = result.json() if result.content else {}
                logger.info("shreckllm_chat_job_result job_id=%s status=succeeded", job_id)
                return data if isinstance(data, dict) else {}
            if status_value == "failed":
                error = (status_data or {}).get("error")
                raise RuntimeError(f"chat job failed job_id={job_id} error={error}")
            if timeout_s is not None and (
                asyncio.get_running_loop().time() - start
            ) >= max(0.1, float(timeout_s)):
                raise httpx.TimeoutException(f"chat job timed out job_id={job_id}")
            await asyncio.sleep(interval)

    def _coerce_target(self, model: str | LLMModelTarget) -> LLMModelTarget:
        if isinstance(model, LLMModelTarget):
            return model
        if isinstance(model, str):
            return LLMModelTarget(provider="openai", name=model.strip() or "gpt-5-nano")
        if isinstance(model, dict):
            provider = str(model.get("provider") or "openai").strip() or "openai"
            name = str(model.get("name") or "gpt-5-nano").strip() or "gpt-5-nano"
            return LLMModelTarget(provider=provider, name=name)
        return LLMModelTarget(provider="openai", name="gpt-5-nano")

    @staticmethod
    def _is_retryable_exception(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code >= 500 or exc.response.status_code == 429
        return False

    def _record_usage_event(
        self,
        *,
        model: str,
        usage: dict[str, Any],
        usage_tag: str | None,
    ) -> None:
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        self._usage_events.append(
            {
                "model": model,
                "prompt_tokens": prompt_tokens if isinstance(prompt_tokens, int) else None,
                "completion_tokens": completion_tokens if isinstance(completion_tokens, int) else None,
                "total_tokens": total_tokens if isinstance(total_tokens, int) else None,
                "input_tokens_est": prompt_tokens if isinstance(prompt_tokens, int) else None,
                "memory_tokens_est": 0 if isinstance(prompt_tokens, int) else None,
                "usage_tag": (usage_tag or "").strip() or None,
            }
        )

    def get_usage_summary(self, *, reset: bool = False) -> dict[str, Any]:
        payload = self._summarize_usage_events(self._usage_events)
        if reset:
            self._usage_events.clear()
        return payload

    def get_usage_event_count(self) -> int:
        return len(self._usage_events)

    def get_usage_summary_since(self, start_index: int) -> dict[str, Any]:
        safe_start = max(0, int(start_index))
        return self._summarize_usage_events(self._usage_events[safe_start:])

    def get_usage_events_since(self, start_index: int) -> list[dict[str, Any]]:
        """Return immutable-facing per-call usage rows for request-level accounting."""
        safe_start = max(0, int(start_index))
        return [dict(event) for event in self._usage_events[safe_start:]]

    def _summarize_usage_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        by_model: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "calls": 0,
                "input_tokens_est": 0,
                "memory_tokens_est": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
            }
        )
        totals = {
            "calls": 0,
            "input_tokens_est": 0,
            "memory_tokens_est": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
        }
        by_tag: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "calls": 0,
                "input_tokens_est": 0,
                "memory_tokens_est": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "by_model": {},
            }
        )

        for event in events:
            model = str(event.get("model") or "unknown")
            row = by_model[model]
            row["calls"] += 1
            totals["calls"] += 1

            input_tokens = event.get("input_tokens_est")
            memory_tokens = event.get("memory_tokens_est")
            output_tokens = event.get("completion_tokens")
            total_tokens = event.get("total_tokens")

            if isinstance(input_tokens, int):
                row["input_tokens_est"] += input_tokens
                totals["input_tokens_est"] += input_tokens
            if isinstance(memory_tokens, int):
                row["memory_tokens_est"] += memory_tokens
                totals["memory_tokens_est"] += memory_tokens
            if isinstance(output_tokens, int):
                row["output_tokens"] += output_tokens
                totals["output_tokens"] += output_tokens
            if isinstance(total_tokens, int):
                row["total_tokens"] += total_tokens
                totals["total_tokens"] += total_tokens

            tag = str(event.get("usage_tag") or "").strip()
            if tag:
                tag_row = by_tag[tag]
                tag_row["calls"] += 1
                if isinstance(input_tokens, int):
                    tag_row["input_tokens_est"] += input_tokens
                if isinstance(memory_tokens, int):
                    tag_row["memory_tokens_est"] += memory_tokens
                if isinstance(output_tokens, int):
                    tag_row["output_tokens"] += output_tokens
                if isinstance(total_tokens, int):
                    tag_row["total_tokens"] += total_tokens
                model_map = tag_row["by_model"]
                model_row = model_map.setdefault(
                    model,
                    {
                        "calls": 0,
                        "input_tokens_est": 0,
                        "memory_tokens_est": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "estimated_cost_usd": 0.0,
                    },
                )
                model_row["calls"] += 1
                if isinstance(input_tokens, int):
                    model_row["input_tokens_est"] += input_tokens
                if isinstance(memory_tokens, int):
                    model_row["memory_tokens_est"] += memory_tokens
                if isinstance(output_tokens, int):
                    model_row["output_tokens"] += output_tokens
                if isinstance(total_tokens, int):
                    model_row["total_tokens"] += total_tokens

        return {
            "totals": totals,
            "by_model": dict(by_model),
            "by_tag": dict(by_tag),
        }
