"""OpenAI client with retries and timeouts."""

import asyncio
import logging
import random
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from openai import (
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
    InternalServerError,
    RateLimitError,
)
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

# Estimated USD price per 1M tokens. Keep this table updated as provider pricing changes.
# If a model is missing, cost is reported as 0 for that model.
MODEL_PRICING_USD_PER_1M: dict[str, dict[str, float]] = {
    "gpt-5": {"input": 1.25, "output": 10.0},
    "gpt-5.1": {"input": 1.25, "output": 10.0},
    "gpt-5-mini": {"input": 0.25, "output": 2.0},
    "gpt-5-nano": {"input": 0.05, "output": 0.4},
    "gpt-4o": {"input": 5.0, "output": 15.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    "gpt-5.4-nano": {"input": 0.05, "output": 0.4},
    "gpt-5.4-mini": {"input": 0.25, "output": 2.0},
}


class OpenAIClient:
    """
    OpenAI client wrapper with retry logic and timeout handling.
    Uses the new Responses API (faster + future-proof).
    """

    def __init__(
        self,
        api_key: str,
        timeout: int = 60,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        # LangChain model cache and in-memory conversation buffers.
        self._lc_models: dict[Tuple[str, float], ChatOpenAI] = {}
        self._conversations: dict[str, list[BaseMessage]] = {}
        self._usage_events: list[dict[str, Any]] = []
        # Extra guard retries for transient network/provider failures.
        # SDK retries remain enabled; these retries run at the wrapper level.
        self.transient_retries = 2

    async def aclose(self) -> None:
        """Best-effort cleanup for cached model clients and conversation buffers."""
        for llm in self._lc_models.values():
            close_fn = getattr(llm, "aclose", None) or getattr(llm, "close", None)
            if close_fn is None:
                continue
            try:
                maybe_coro = close_fn()
                if asyncio.iscoroutine(maybe_coro):
                    await maybe_coro
            except RuntimeError as exc:
                if "event loop is closed" not in str(exc).lower():
                    raise
                logger.debug("Ignoring loop-closed error during OpenAI client cleanup: %s", exc)

        self._lc_models.clear()
        self._conversations.clear()
        self._usage_events.clear()

    async def __aenter__(self) -> "OpenAIClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        await self.aclose()

    async def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        conversation_id: Optional[str] = None,
        use_conversation_memory: bool = False,
        usage_tag: Optional[str] = None,
        return_metadata: bool = False,
    ) -> str | dict[str, Any]:
        """
        Send a request using the Responses API.

        Args:
            model: e.g. "gpt-5", "gpt-5-mini", "gpt-5-nano"
            messages: [{"role": "system"|"user"|"assistant", "content": "..."}, ...]
            temperature: sampling temperature
            max_tokens: cap on output tokens (mapped to max_output_tokens)

        Returns:
            str: model text
        """
        # some GPT-5-family models pin temp to 1.0
        restricted_models = {"gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5.1", "gpt-5.2"}
        if model in restricted_models and temperature != 1.0:
            logger.warning(
                "Temperature %.2f is not supported for model %s; using 1.0 instead",
                temperature,
                model,
            )
            temperature = 1.0

        lc_messages = self._to_langchain_messages(messages)
        if conversation_id and use_conversation_memory:
            history = self._conversations.setdefault(conversation_id, [])
            input_messages = [*history, *lc_messages]
        else:
            input_messages = lc_messages

        attempts = self.transient_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                llm = self._get_langchain_model(
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                resp = await llm.ainvoke(input_messages)
                text = self._extract_text_from_langchain_response(resp)
                usage = self._extract_usage_metadata(resp)
                response_metadata = self._extract_response_metadata(resp)
                if conversation_id and use_conversation_memory:
                    # Persist only compact role-structured messages for this run thread.
                    self._conversations.setdefault(conversation_id, []).extend(
                        [*lc_messages, AIMessage(content=text)]
                    )
                self._record_usage_event(
                    model=model,
                    usage=usage,
                    use_conversation_memory=use_conversation_memory,
                    current_messages=lc_messages,
                    history_messages=history if conversation_id and use_conversation_memory else [],
                    usage_tag=usage_tag,
                )
                if return_metadata:
                    return {
                        "text": text,
                        "usage": usage,
                        "response_metadata": response_metadata,
                    }
                return text
            except Exception as e:
                if not self._is_retryable_exception(e) or attempt >= attempts:
                    logger.error("OpenAI/LangChain chat error for model %s: %s", model, e)
                    raise
                sleep_seconds = min(12.0, (2 ** (attempt - 1)) + random.uniform(0.1, 0.7))
                logger.warning(
                    "Transient OpenAI error for model %s (attempt %d/%d): %s. Retrying in %.2fs",
                    model,
                    attempt,
                    attempts,
                    e,
                    sleep_seconds,
                )
                await asyncio.sleep(sleep_seconds)

        raise RuntimeError("Unreachable retry loop state in OpenAIClient.chat")

    def _record_usage_event(
        self,
        *,
        model: str,
        usage: dict[str, int | None],
        use_conversation_memory: bool,
        current_messages: list[BaseMessage],
        history_messages: list[BaseMessage],
        usage_tag: str | None,
    ) -> None:
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")

        input_tokens_est: int | None = prompt_tokens if isinstance(prompt_tokens, int) else None
        memory_tokens_est: int | None = 0 if isinstance(prompt_tokens, int) else None
        if isinstance(prompt_tokens, int) and use_conversation_memory and history_messages:
            current_chars = sum(len(str(getattr(msg, "content", "") or "")) for msg in current_messages)
            history_chars = sum(len(str(getattr(msg, "content", "") or "")) for msg in history_messages)
            total_chars = current_chars + history_chars
            if total_chars > 0:
                memory_tokens_est = int(round(prompt_tokens * (history_chars / total_chars)))
                input_tokens_est = max(0, prompt_tokens - memory_tokens_est)

        self._usage_events.append(
            {
                "model": model,
                "prompt_tokens": prompt_tokens if isinstance(prompt_tokens, int) else None,
                "completion_tokens": completion_tokens if isinstance(completion_tokens, int) else None,
                "total_tokens": total_tokens if isinstance(total_tokens, int) else None,
                "input_tokens_est": input_tokens_est,
                "memory_tokens_est": memory_tokens_est,
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
                tag_models = tag_row["by_model"]
                model_row = tag_models.setdefault(
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

        for model, row in by_model.items():
            pricing = MODEL_PRICING_USD_PER_1M.get(model, {})
            input_price = float(pricing.get("input", 0.0))
            output_price = float(pricing.get("output", 0.0))
            prompt_total = row["input_tokens_est"] + row["memory_tokens_est"]
            estimated_cost = (prompt_total / 1_000_000.0) * input_price + (
                row["output_tokens"] / 1_000_000.0
            ) * output_price
            row["estimated_cost_usd"] = round(estimated_cost, 6)
            totals["estimated_cost_usd"] += estimated_cost

        totals["estimated_cost_usd"] = round(totals["estimated_cost_usd"], 6)
        for tag, tag_row in by_tag.items():
            tag_models = tag_row["by_model"]
            for model, model_row in tag_models.items():
                pricing = MODEL_PRICING_USD_PER_1M.get(model, {})
                input_price = float(pricing.get("input", 0.0))
                output_price = float(pricing.get("output", 0.0))
                prompt_total = model_row["input_tokens_est"] + model_row["memory_tokens_est"]
                model_estimated_cost = (prompt_total / 1_000_000.0) * input_price + (
                    model_row["output_tokens"] / 1_000_000.0
                ) * output_price
                model_row["estimated_cost_usd"] = round(model_estimated_cost, 6)
                tag_row["estimated_cost_usd"] += model_estimated_cost
            tag_row["estimated_cost_usd"] = round(tag_row["estimated_cost_usd"], 6)
            tag_row["by_model"] = dict(sorted(tag_models.items(), key=lambda kv: kv[0]))
        payload = {
            "totals": totals,
            "by_model": dict(sorted(by_model.items(), key=lambda kv: kv[0])),
            "by_tag": dict(sorted(by_tag.items(), key=lambda kv: kv[0])),
        }
        return payload

    @staticmethod
    def _to_langchain_messages(messages: List[Dict[str, str]]) -> list[BaseMessage]:
        out: list[BaseMessage] = []
        for msg in messages:
            role = (msg.get("role") or "").strip().lower()
            content = str(msg.get("content", ""))
            if role == "system":
                out.append(SystemMessage(content=content))
            elif role == "assistant":
                out.append(AIMessage(content=content))
            else:
                out.append(HumanMessage(content=content))
        return out

    @staticmethod
    def _extract_text_from_langchain_response(resp: Any) -> str:
        content = getattr(resp, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text_value = item.get("text")
                    if isinstance(text_value, str):
                        parts.append(text_value)
            return "\n".join([p for p in parts if p]).strip()
        return str(content or "")

    @staticmethod
    def _extract_usage_metadata(resp: Any) -> dict[str, int | None]:
        usage_meta = getattr(resp, "usage_metadata", None)
        if isinstance(usage_meta, dict):
            prompt_tokens = usage_meta.get("input_tokens")
            completion_tokens = usage_meta.get("output_tokens")
            total_tokens = usage_meta.get("total_tokens")
            return {
                "prompt_tokens": int(prompt_tokens) if isinstance(prompt_tokens, int) else None,
                "completion_tokens": int(completion_tokens)
                if isinstance(completion_tokens, int)
                else None,
                "total_tokens": int(total_tokens) if isinstance(total_tokens, int) else None,
            }

        response_meta = getattr(resp, "response_metadata", None)
        if isinstance(response_meta, dict):
            token_usage = response_meta.get("token_usage")
            if isinstance(token_usage, dict):
                prompt_tokens = token_usage.get("prompt_tokens")
                completion_tokens = token_usage.get("completion_tokens")
                total_tokens = token_usage.get("total_tokens")
                return {
                    "prompt_tokens": int(prompt_tokens)
                    if isinstance(prompt_tokens, int)
                    else None,
                    "completion_tokens": int(completion_tokens)
                    if isinstance(completion_tokens, int)
                    else None,
                    "total_tokens": int(total_tokens) if isinstance(total_tokens, int) else None,
                }
        return {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }

    @staticmethod
    def _extract_response_metadata(resp: Any) -> dict[str, Any]:
        response_meta = getattr(resp, "response_metadata", None)
        return response_meta if isinstance(response_meta, dict) else {}

    def _get_langchain_model(
        self,
        *,
        model: str,
        temperature: float,
        max_tokens: Optional[int],
    ) -> ChatOpenAI:
        key = (model, temperature)
        cached = self._lc_models.get(key)
        if cached is not None:
            return cached
        llm = ChatOpenAI(
            api_key=self.api_key,
            model=model,
            temperature=temperature,
            timeout=self.timeout,
            max_retries=self.max_retries,
            max_tokens=max_tokens,
        )
        self._lc_models[key] = llm
        return llm

    @staticmethod
    def _is_retryable_exception(exc: Exception) -> bool:
        if isinstance(
            exc,
            (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError),
        ):
            return True
        if isinstance(exc, APIStatusError):
            status = getattr(exc, "status_code", None)
            return status in {408, 409, 429, 500, 502, 503, 504}
        return False

