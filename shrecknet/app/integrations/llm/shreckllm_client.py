"""shreckLLM HTTP client with OpenAIClient-compatible surface."""

from __future__ import annotations

import asyncio
import logging
import random
from collections import defaultdict
from typing import Any

import httpx

from app.core.config_store import LLMModelTarget

logger = logging.getLogger(__name__)


class ShreckLLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self._max_backoff_s = 20.0
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)
        self._usage_events: list[dict[str, Any]] = []

    async def aclose(self) -> None:
        await self._http.aclose()
        self._usage_events.clear()

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
    ) -> str | dict[str, Any]:
        target = self._coerce_target(model)
        payload: dict[str, Any] = {
            "provider_id": target.provider,
            "model": target.name,
            "messages": messages,
            "temperature": temperature,
            "conversation_id": conversation_id,
            "use_conversation_memory": use_conversation_memory,
            "metadata": {"usage_tag": usage_tag} if usage_tag else None,
        }
        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = await self._http.post("/chat", json=payload)
                response.raise_for_status()
                data = response.json()
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
                await asyncio.sleep(
                    min(
                        self._max_backoff_s,
                        (2 ** attempt) + random.uniform(0.25, 1.25),
                    )
                )

        raise RuntimeError("Unreachable retry loop state in ShreckLLMClient.chat")

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
