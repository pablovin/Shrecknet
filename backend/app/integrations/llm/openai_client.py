"""OpenAI client with retries and timeouts."""

import asyncio
import logging
import random
from typing import Any, Dict, List, Optional

from openai import (
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

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
        self._client: AsyncOpenAI = AsyncOpenAI(
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
        # Extra guard retries for transient network/provider failures.
        # SDK retries remain enabled; these retries run at the wrapper level.
        self.transient_retries = 2

    async def aclose(self) -> None:
        """Close the underlying HTTP client to avoid loop shutdown errors."""
        # AsyncOpenAI exposes `close()`; some versions omit `aclose()`
        close_fn = getattr(self._client, "aclose", None) or getattr(
            self._client, "close", None
        )
        if close_fn is None:
            return
        maybe_coro = close_fn()
        if asyncio.iscoroutine(maybe_coro):
            await maybe_coro

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

        req: Dict[str, Any] = {
            "model": model,
            "input": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            req["max_output_tokens"] = max_tokens

        attempts = self.transient_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                resp = await self._client.responses.create(**req)
                try:
                    return resp.output[0].content[0].text  # type: ignore[attr-defined]
                except Exception:
                    return getattr(resp, "output_text", "") or ""
            except Exception as e:
                if not self._is_retryable_exception(e) or attempt >= attempts:
                    logger.error("OpenAI Responses API error for model %s: %s", model, e)
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

