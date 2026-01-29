"""OpenAI client with retries and timeouts."""

import asyncio
import logging
from typing import Any, Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


from typing import Any, Optional, List, Dict
from openai import AsyncOpenAI
import logging

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
        try:
            # some GPT-5-family models pin temp to 1.0
            restricted_models = {"gpt-5", "gpt-5-mini", "gpt-5-nano"}
            if model in restricted_models and temperature != 1.0:
                logger.warning(
                    "Temperature %.2f is not supported for model %s; using 1.0 instead",
                    temperature,
                    model,
                )
                temperature = 1.0

            # Responses API uses `input=...` for everything chat-like
            # we can just pass our messages array directly
            req: Dict[str, Any] = {
                "model": model,
                "input": messages,  # chat-style input
                "temperature": temperature,
            }
            if max_tokens is not None:
                # in Responses API this is called max_output_tokens
                req["max_output_tokens"] = max_tokens

            resp = await self._client.responses.create(**req)

            # Responses API nests output a bit differently.
            # Try to extract the main text safely.
            text = ""
            try:
                # usual shape: resp.output[0].content[0].text
                text = resp.output[0].content[0].text  # type: ignore[attr-defined]
            except Exception:
                # fallback: sometimes SDK gives a convenience property
                text = getattr(resp, "output_text", "") or ""
            return text

        except Exception as e:
            logger.error(f"OpenAI Responses API error for model {model}: {e}")
            raise

