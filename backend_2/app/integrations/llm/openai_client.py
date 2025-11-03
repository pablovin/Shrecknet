"""OpenAI client with retries and timeouts."""

import asyncio
import logging
from typing import Any, Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class OpenAIClient:
    """
    OpenAI client wrapper with retry logic and timeout handling.
    Uses native OpenAI SDK for optimal performance and latest features.
    """

    def __init__(
        self,
        api_key: str,
        timeout: int = 60,
        max_retries: int = 3,
    ):
        """
        Initialize OpenAI client.

        Args:
            api_key: OpenAI API key
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries
        """
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: AsyncOpenAI = AsyncOpenAI(
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Send a chat completion request using OpenAI's Chat Completions API.

        Args:
            model: Model name (e.g., "gpt-4o", "gpt-4o-mini")
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens in response

        Returns:
            Generated text response

        Raises:
            Exception: On API errors after retries
        """
        try:
            # Validate temperature for restricted models
            restricted_models = {"gpt-5", "gpt-5-mini", "gpt-5-nano"}
            if model in restricted_models and temperature != 1.0:
                logger.warning(
                    "Temperature %.2f is not supported for model %s; using 1.0 instead",
                    temperature,
                    model,
                )
                temperature = 1.0

            # Call OpenAI API
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens

            response = await self._client.chat.completions.create(**kwargs)

            return response.choices[0].message.content or ""

        except Exception as e:
            logger.error(f"OpenAI API error for model {model}: {e}")
            raise
