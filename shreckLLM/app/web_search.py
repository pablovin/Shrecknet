"""Shared provider search capability and admission; agents never send provider tools."""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.provider_registry import ProviderAdapter

from app.errors import ProviderBadRequestError


def capability(adapter: ProviderAdapter, model: str) -> bool | None:
    if adapter.provider_id != "openrouter":
        return False
    parameters = getattr(adapter, "model_parameters", {}).get(model)
    return "tools" in parameters if isinstance(parameters, list) else None


async def validate_web_search(adapter: ProviderAdapter, model: str, enabled: bool) -> None:
    if not enabled:
        return
    if adapter.provider_id == "openrouter" and capability(adapter, model) is None:
        await adapter.list_models()
    if capability(adapter, model) is not True:
        raise ProviderBadRequestError("web_search_unsupported: choose a search-capable model from the catalog")


def search_tools() -> list[dict[str, Any]]:
    # Pin Exa so native-provider routing cannot bypass the per-request limits.
    return [{"type": "openrouter:web_search", "parameters": {
        "engine": "exa", "max_results": 6, "max_total_results": 6,
        "max_uses": 1, "max_characters": 4000,
    }}]


def annotations(message: Any) -> list[dict[str, Any]]:
    """Keep only bounded provider URL citations; no model-generated source lists."""
    values = getattr(message, "annotations", None) or []
    result = []
    for value in values[:25]:
        row = value.model_dump() if hasattr(value, "model_dump") else value
        if not isinstance(row, dict) or row.get("type") != "url_citation":
            continue
        citation = row.get("url_citation")
        if not isinstance(citation, dict) or not isinstance(citation.get("url"), str):
            continue
        result.append({"type": "url_citation", "url_citation": {
            key: citation[key][:limit] for key, limit in (("url", 4000), ("title", 500), ("content", 4000))
            if isinstance(citation.get(key), str)
        }})
    return result
