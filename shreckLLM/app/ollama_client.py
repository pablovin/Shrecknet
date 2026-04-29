from __future__ import annotations

from typing import Any

import httpx

from app.errors import (
    DependencyUnavailableError,
    InvalidModelError,
    ProviderOverloadedError,
    ProviderTimeoutError,
)
from app.schemas import ChatMessage


class OllamaClient:
    provider_id = "ollama"
    def __init__(self, *, base_url: str, timeout_s: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        try:
            response = await self._client.get("/api/tags")
            return response.status_code == 200
        except Exception:
            return False

    async def health(self) -> bool:
        return await self.ping()

    async def list_models(self) -> list[str]:
        payload = await self._request_json("GET", "/api/tags")
        models = payload.get("models", []) if isinstance(payload, dict) else []
        out: list[str] = []
        for item in models:
            if isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str) and name:
                    out.append(name)
        return out

    async def ensure_model_available(self, model: str) -> bool:
        available = await self.list_models()
        return model in set(available)

    async def chat(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": float(temperature)}
        if max_tokens is not None:
            options["num_predict"] = int(max_tokens)

        payload = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "stream": False,
            "options": options,
        }
        data = await self._chat_request(payload)
        if not isinstance(data, dict):
            raise DependencyUnavailableError("invalid response from ollama")
        message = data.get("message") or {}
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str):
            text = ""

        prompt_tokens = data.get("prompt_eval_count")
        completion_tokens = data.get("eval_count")
        total_tokens = None
        if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            total_tokens = prompt_tokens + completion_tokens

        return {
            "text": text,
            "provider_request_id": None,
            "usage": {
                "prompt_tokens": prompt_tokens if isinstance(prompt_tokens, int) else None,
                "completion_tokens": completion_tokens if isinstance(completion_tokens, int) else None,
                "total_tokens": total_tokens,
            },
            "raw": data,
        }

    async def _chat_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            data = await self._request_json("POST", "/api/chat", json=payload, map_404_to_invalid_model=False)
            if isinstance(data, dict):
                return data
        except InvalidModelError:
            # If endpoint exists but model is missing, keep model-not-found behavior.
            raise
        except DependencyUnavailableError:
            # Some Ollama builds expose only /api/generate. Try compatibility fallback.
            pass

        generate_payload = {
            "model": payload["model"],
            "prompt": self._messages_to_prompt(payload.get("messages", [])),
            "stream": False,
            "options": payload.get("options") or {},
        }
        data = await self._request_json("POST", "/api/generate", json=generate_payload)
        if not isinstance(data, dict):
            raise DependencyUnavailableError("invalid response from ollama")
        if "message" not in data:
            response_text = data.get("response")
            if isinstance(response_text, str):
                data["message"] = {"content": response_text}
        return data

    @staticmethod
    def _messages_to_prompt(messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for item in messages:
            role = str(item.get("role", "user")).upper()
            content = str(item.get("content", "")).strip()
            if content:
                lines.append(f"{role}: {content}")
        lines.append("ASSISTANT:")
        return "\n".join(lines)

    async def _request_json(self, method: str, path: str, *, map_404_to_invalid_model: bool = True, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("ollama request timed out") from exc
        except httpx.ConnectError as exc:
            raise DependencyUnavailableError("ollama is unreachable") from exc
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError("ollama http error") from exc

        if response.status_code == 404 and map_404_to_invalid_model:
            raise InvalidModelError("model not found")
        if response.status_code == 404:
            raise DependencyUnavailableError("ollama endpoint not found")
        if response.status_code in {408, 429, 503, 504}:
            raise ProviderOverloadedError("ollama overloaded")
        if response.status_code >= 500:
            raise DependencyUnavailableError("ollama internal error")
        if response.status_code >= 400:
            raise DependencyUnavailableError(f"ollama request failed ({response.status_code})")

        return response.json()
