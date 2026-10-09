"""Deterministic, owner-scoped CharacterAgent memory rendering and ranking.

This module deliberately accepts only already-owned ``ScenePerspective``
aggregates.  It never follows a perspective back into its canonical Scene or
out into the wider ontology, so a character query cannot acquire objective or
another character's knowledge while retrieving memories.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Any

from app.graphrag.embedding_service import EmbeddingService


def render_memory_document(memory: dict[str, Any]) -> str:
    """Render the searchable subjective record without opaque graph IDs."""
    lines = [
        "Perspective:", str(memory.get("perspective") or ""),
    ]
    emotions = memory.get("emotions") or []
    if emotions:
        lines.append("Emotional residue:")
        lines.extend(f"- {item.get('description', '')}" for item in emotions)
    beliefs = memory.get("beliefs") or []
    if beliefs:
        lines.append("Beliefs:")
        lines.extend(f"- {item.get('statement', '')}" for item in beliefs)
    impacts = memory.get("impacts") or []
    if impacts:
        lines.append("Lasting consequences:")
        lines.extend(
            f"- {item.get('impact_type', '')}/{item.get('direction', '')}: "
            f"{item.get('description', '')}"
            + (f" concerning {item['target_name']}" if item.get("target_name") else "")
            for item in impacts
        )
    return "\n".join(part.strip() for part in lines if str(part).strip())


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w']+", text.casefold()))


def _cosine(left: list[float], right: list[float]) -> float | None:
    if not left or len(left) != len(right):
        return None
    value = sum(a * b for a, b in zip(left, right))
    if not math.isfinite(value):
        return None
    return max(-1.0, min(1.0, value))


async def select_relevant_memories(
    *, query: str, memories: list[dict[str, Any]], context: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None, limit: int = 5,
) -> list[dict[str, Any]]:
    """Return a stable top-N subset, falling back to lexical ranking safely."""
    if not memories:
        return []
    # Recall should reflect the whole decision situation, not just a short verb
    # like "choose". Include provided options/context and current identity cues
    # so memory matches can be about people, stakes, and goals in the decision.
    identity_cues = identity or {}
    if identity:
        # The generated identity and current goals/aspects are directly useful
        # for matching subjective history. Avoid broad vocabulary from every
        # trait label; keep only compact identity fields to reduce recall noise.
        character = identity.get("character") or {}
        identity_cues = {
            "name": character.get("name"),
            "background_story": character.get("background_story"),
            "identity_description": character.get("identity_description"),
            "aspects": character.get("aspects"),
            "goals": character.get("goals"),
        }
    retrieval_query = "\n".join(part for part in (
        query,
        json.dumps(context, ensure_ascii=False, sort_keys=True) if context else "",
        json.dumps(identity_cues, ensure_ascii=False, sort_keys=True) if identity_cues else "",
    ) if part)
    query_vector: list[float] | None = None
    if any(isinstance(item.get("memory_embedding"), list) for item in memories):
        try:
            query_vector = await asyncio.to_thread(EmbeddingService().embed_text, retrieval_query)
        except Exception:
            # Memory retrieval remains available while an embedding runtime is
            # being repaired or backfilled; it must never broaden scope.
            query_vector = None
    query_tokens = _tokens(retrieval_query)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for item in memories:
        document = str(item.get("memory_document") or render_memory_document(item))
        overlap = len(query_tokens & _tokens(document)) / max(1, len(query_tokens))
        semantic = _cosine(query_vector or [], item.get("memory_embedding") or [])
        relevance = (0.82 * max(0.0, semantic) + 0.18 * overlap) if semantic is not None else overlap
        score = relevance
        # Low-information queries must not inject arbitrary memories.
        if relevance >= 0.12:
            ranked.append((score, item))
    ranked.sort(key=lambda row: (-row[0], str(row[1].get("id") or "")))
    allowed = (
        "perspective", "source_type", "emotions", "beliefs",
        "impacts",
    )
    # Ranking needs internal IDs/vectors, but public prompting does not. Keeping
    # the projection here makes accidental identifier leakage difficult.
    return [{key: item.get(key) for key in allowed if item.get(key) is not None}
            for _, item in ranked[:limit]]
