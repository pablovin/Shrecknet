"""OpenAI client with retries and timeouts."""

import asyncio
import logging
import random
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
    ) -> str:
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
                if conversation_id and use_conversation_memory:
                    # Persist only compact role-structured messages for this run thread.
                    self._conversations.setdefault(conversation_id, []).extend(
                        [*lc_messages, AIMessage(content=text)]
                    )
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

