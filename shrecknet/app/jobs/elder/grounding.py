"""Compact request grounding for Elder v2 planning."""

from __future__ import annotations

from typing import Any


_ONTOLOGY_GROUNDING_CACHE: dict[tuple[tuple[int, ...], int], dict[str, Any]] = {}


async def build_grounding_package(
    *,
    retriever: Any,
    ontology_ids: list[int],
    instance_id: str | None,
    definitions: list[dict[str, Any]] | None,
    resolved_entities: list[dict[str, Any]],
    chat_history: list[dict[str, str]] | None,
) -> dict[str, Any]:
    structured_definitions = list(definitions or [])
    definitions_key = repr(structured_definitions)
    cache_key = (tuple(sorted(ontology_ids)), hash(definitions_key))
    immutable = _ONTOLOGY_GROUNDING_CACHE.get(cache_key)
    if immutable is None:
        immutable = {"ontology_ids": ontology_ids, "definitions": structured_definitions}
        # Small process-local cache; ontology edits naturally change the definitions hash.
        if len(_ONTOLOGY_GROUNDING_CACHE) >= 64:
            _ONTOLOGY_GROUNDING_CACHE.pop(next(iter(_ONTOLOGY_GROUNDING_CACHE)))
        _ONTOLOGY_GROUNDING_CACHE[cache_key] = immutable
    return {
        **immutable,
        "active_instance_id": instance_id,
        "resolved_entities": resolved_entities,
    }
