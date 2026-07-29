from __future__ import annotations

import logging
from typing import Any

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError

from app.errors import (
    DependencyUnavailableError,
    InvalidModelError,
    ProviderAuthenticationError,
    ProviderBadRequestError,
    ProviderOverloadedError,
    ProviderPermissionError,
    ProviderTimeoutError,
)
from app.schemas import ChatMessage

logger = logging.getLogger(__name__)


def _openrouter_nitro_model(model: str) -> str:
    """Apply OpenRouter throughput routing once to the selected model."""
    cleaned = (model or "").strip()
    if cleaned.casefold().endswith(":nitro"):
        return f"{cleaned[:-6]}:nitro"
    return f"{cleaned}:nitro"


class OpenAIClient:
    provider_id = "openai"
    def __init__(
        self,
        *,
        api_key: str,
        timeout_s: float,
        base_url: str | None = None,
        provider_id: str = "openai",
    ) -> None:
        self.provider_id = provider_id
        self._api_key = (api_key or "").strip()
        self._client: AsyncOpenAI | None = None
        self._timeout_s = float(timeout_s)
        self._base_url = base_url

        if self._api_key:
            kwargs: dict[str, Any] = {
                "api_key": self._api_key,
                "timeout": self._timeout_s,
                # shreckLLM owns retries so one logical job has one observable
                # retry policy and never duplicates attempts inside the SDK.
                "max_retries": 0,
            }
            if base_url:
                kwargs["base_url"] = base_url
            self._client = AsyncOpenAI(**kwargs)

    @property
    def configured(self) -> bool:
        return self._client is not None

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    async def aclose(self) -> None:
        if self._client is None:
            return
        close_fn = getattr(self._client, "aclose", None) or getattr(self._client, "close", None)
        if close_fn is None:
            return
        maybe_coro = close_fn()
        if hasattr(maybe_coro, "__await__"):
            await maybe_coro

    async def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.models.list()
            return True
        except Exception:
            return False

    async def health(self) -> bool:
        return await self.ping()

    async def validate_api_key(self) -> dict[str, Any]:
        if not self.has_api_key:
            return {
                "configured": False,
                "present": False,
                "valid": None,
                "error": "missing_api_key",
            }
        if self._client is None:
            return {
                "configured": False,
                "present": True,
                "valid": None,
                "error": "client_not_initialized",
            }
        try:
            await self._client.models.list()
            return {
                "configured": True,
                "present": True,
                "valid": True,
                "error": None,
            }
        except APITimeoutError:
            return {
                "configured": True,
                "present": True,
                "valid": None,
                "error": "timeout",
            }
        except APIConnectionError:
            return {
                "configured": True,
                "present": True,
                "valid": None,
                "error": "connection_error",
            }
        except RateLimitError:
            return {
                "configured": True,
                "present": True,
                "valid": True,
                "error": "rate_limited",
            }
        except Exception as exc:
            text = str(exc).lower()
            if "api key" in text and ("invalid" in text or "incorrect" in text):
                return {
                    "configured": True,
                    "present": True,
                    "valid": False,
                    "error": "invalid_api_key",
                }
            if "401" in text:
                return {
                    "configured": True,
                    "present": True,
                    "valid": False,
                    "error": "unauthorized",
                }
            return {
                "configured": True,
                "present": True,
                "valid": None,
                "error": "unknown_error",
            }

    async def list_models(self) -> list[str]:
        if self._client is None:
            return []
        try:
            resp = await self._client.models.list()
        except APITimeoutError as exc:
            raise ProviderTimeoutError(f"{self.provider_id} request timed out") from exc
        except RateLimitError as exc:
            raise ProviderOverloadedError(f"{self.provider_id} rate limited") from exc
        except APIConnectionError as exc:
            raise DependencyUnavailableError(f"{self.provider_id} is unreachable") from exc
        except Exception as exc:
            raise DependencyUnavailableError(f"{self.provider_id} models list failed") from exc

        items = getattr(resp, "data", []) or []
        out: list[str] = []
        for item in items:
            model_id = getattr(item, "id", None)
            if isinstance(model_id, str) and model_id:
                out.append(model_id)
        return sorted(set(out))

    async def chat(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._client is None:
            raise DependencyUnavailableError(f"{self.provider_id} is not configured")

        execution_model = (
            _openrouter_nitro_model(model)
            if self.provider_id == "openrouter"
            else model
        )
        kwargs: dict[str, Any] = {
            "model": execution_model,
            "messages": [m.model_dump() for m in messages],
        }
        # GPT-5-family chat models may reject non-default temperature values.
        # Omit the parameter entirely for those models so the provider default is used.
        normalized_model = (model or "").strip().lower()
        if not normalized_model.startswith("gpt-5"):
            kwargs["temperature"] = float(temperature)
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = int(max_tokens)
        if response_format is not None:
            kwargs["response_format"] = response_format
        if self.provider_id == "deepinfra":
            # DeepInfra calls its normal, non-surcharged service tier "default".
            # Keep this explicit so requests never opt into priority inference.
            kwargs["extra_body"] = {"service_tier": "default"}

        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except APITimeoutError as exc:
            raise ProviderTimeoutError(f"{self.provider_id} request timed out") from exc
        except RateLimitError as exc:
            raise ProviderOverloadedError(f"{self.provider_id} rate limited") from exc
        except APIConnectionError as exc:
            raise DependencyUnavailableError(f"{self.provider_id} is unreachable") from exc
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            text = str(exc).lower()
            logger.exception(
                "openai chat request failed status_code=%s error_type=%s",
                status_code,
                type(exc).__name__,
            )
            if status_code == 400 or ("model" in text and "not" in text and "found" in text):
                raise InvalidModelError("model not found") from exc
            if status_code == 401:
                raise ProviderAuthenticationError(f"{self.provider_id} authentication failed") from exc
            if status_code == 403:
                raise ProviderPermissionError(f"{self.provider_id} permission denied") from exc
            if status_code == 429:
                raise ProviderOverloadedError(f"{self.provider_id} rate limited") from exc
            if status_code == 400:
                raise ProviderBadRequestError(f"{self.provider_id} rejected request") from exc
            raise DependencyUnavailableError(f"{self.provider_id} request failed") from exc

        text = ""
        choices = getattr(resp, "choices", None) or []
        if choices:
            first = choices[0]
            message = getattr(first, "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str):
                text = content

        usage_obj = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage_obj, "prompt_tokens", None)
        completion_tokens = getattr(usage_obj, "completion_tokens", None)
        total_tokens = getattr(usage_obj, "total_tokens", None)

        return {
            "text": text,
            "provider_request_id": getattr(resp, "id", None),
            "usage": {
                "prompt_tokens": int(prompt_tokens) if isinstance(prompt_tokens, int) else None,
                "completion_tokens": int(completion_tokens)
                if isinstance(completion_tokens, int)
                else None,
                "total_tokens": int(total_tokens) if isinstance(total_tokens, int) else None,
            },
            "raw": resp,
        }
