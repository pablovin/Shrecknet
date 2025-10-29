"""OpenAI client with retries and timeouts."""

import asyncio
import logging
from typing import Any, Optional

from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


class OpenAIClient:
    """
    OpenAI client wrapper with retry logic and timeout handling.
    Uses LangChain's ChatOpenAI for better integration.
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
        self._clients: dict[str, ChatOpenAI] = {}

    def _get_client(self, model: str, temperature: float = 0.7) -> ChatOpenAI:
        """Get or create a ChatOpenAI client for the specified model."""
        cache_key = f"{model}_{temperature}"
        if cache_key not in self._clients:
            self._clients[cache_key] = ChatOpenAI(
                model=model,
                temperature=temperature,
                timeout=self.timeout,
                max_retries=self.max_retries,
                api_key=self.api_key,
            )
        return self._clients[cache_key]

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Send a chat completion request.

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
            client = self._get_client(model, temperature)

            # Convert messages to LangChain format
            from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

            lc_messages = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")

                if role == "system":
                    lc_messages.append(SystemMessage(content=content))
                elif role == "assistant":
                    lc_messages.append(AIMessage(content=content))
                else:  # user or default
                    lc_messages.append(HumanMessage(content=content))

            # Invoke the model
            response = await asyncio.to_thread(client.invoke, lc_messages)

            return response.content

        except Exception as e:
            logger.error(f"OpenAI API error for model {model}: {e}")
            raise
