from __future__ import annotations

from typing import Any, Protocol

from app.schemas import ChatMessage


class ProviderAdapter(Protocol):
    provider_id: str

    async def chat(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]: ...

    async def list_models(self) -> list[str]: ...

    async def health(self) -> bool: ...


class ProviderRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}

    def register(self, adapter: ProviderAdapter) -> None:
        self._adapters[adapter.provider_id] = adapter

    def get(self, provider_id: str) -> ProviderAdapter | None:
        return self._adapters.get(provider_id)

    def provider_ids(self) -> list[str]:
        return sorted(self._adapters.keys())
