from __future__ import annotations

from typing import Any

import httpx

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


class AnthropicClient:
    provider_id = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        timeout_s: float,
        base_url: str | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self._timeout_s = float(timeout_s)
        self._client: httpx.AsyncClient | None = None
        if self._api_key:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_s,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                },
            )

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def health(self) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.get("/v1/models")
            return True
        except Exception:
            return False

    async def validate_api_key(self) -> dict[str, Any]:
        if not self.has_api_key:
            return {"configured": False, "present": False, "valid": None, "error": "missing_api_key"}
        if self._client is None:
            return {"configured": False, "present": True, "valid": None, "error": "client_not_initialized"}
        try:
            resp = await self._client.get("/v1/models")
            if resp.status_code == 401:
                return {"configured": True, "present": True, "valid": False, "error": "unauthorized"}
            if resp.status_code in (429,):
                return {"configured": True, "present": True, "valid": True, "error": "rate_limited"}
            resp.raise_for_status()
            return {"configured": True, "present": True, "valid": True, "error": None}
        except httpx.TimeoutException:
            return {"configured": True, "present": True, "valid": None, "error": "timeout"}
        except httpx.HTTPError:
            return {"configured": True, "present": True, "valid": None, "error": "connection_error"}

    async def list_models(self) -> list[str]:
        if self._client is None:
            return []
        try:
            resp = await self._client.get("/v1/models")
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("anthropic request timed out") from exc
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError("anthropic is unreachable") from exc

        if resp.status_code == 401:
            raise ProviderAuthenticationError("anthropic authentication failed")
        if resp.status_code == 429:
            raise ProviderOverloadedError("anthropic rate limited")
        if resp.status_code >= 500:
            raise DependencyUnavailableError("anthropic models list failed")
        if resp.status_code >= 400:
            raise ProviderBadRequestError("anthropic rejected request")

        data = resp.json().get("data") if isinstance(resp.json(), dict) else None
        out: list[str] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    model_id = item.get("id")
                    if isinstance(model_id, str) and model_id:
                        out.append(model_id)
        return sorted(set(out))

    async def chat(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
    ) -> dict[str, Any]:
        if self._client is None:
            raise DependencyUnavailableError("anthropic is not configured")

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": float(temperature),
            "max_tokens": 1024,
        }

        try:
            resp = await self._client.post("/v1/messages", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("anthropic request timed out") from exc
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError("anthropic is unreachable") from exc

        if resp.status_code == 400:
            text = resp.text.lower()
            if "model" in text and "not" in text and "found" in text:
                raise InvalidModelError("model not found")
            raise ProviderBadRequestError("anthropic rejected request")
        if resp.status_code == 401:
            raise ProviderAuthenticationError("anthropic authentication failed")
        if resp.status_code == 403:
            raise ProviderPermissionError("anthropic permission denied")
        if resp.status_code == 429:
            raise ProviderOverloadedError("anthropic rate limited")
        if resp.status_code >= 500:
            raise DependencyUnavailableError("anthropic request failed")
        if resp.status_code >= 400:
            raise ProviderBadRequestError("anthropic rejected request")

        data = resp.json()
        text = ""
        content = data.get("content") if isinstance(data, dict) else None
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                    text += part["text"]

        usage = data.get("usage") if isinstance(data, dict) else {}
        input_tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
        output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
        total = (input_tokens + output_tokens) if isinstance(input_tokens, int) and isinstance(output_tokens, int) else None

        return {
            "text": text,
            "provider_request_id": data.get("id") if isinstance(data, dict) else None,
            "usage": {
                "prompt_tokens": int(input_tokens) if isinstance(input_tokens, int) else None,
                "completion_tokens": int(output_tokens) if isinstance(output_tokens, int) else None,
                "total_tokens": int(total) if isinstance(total, int) else None,
            },
            "raw": data,
        }
