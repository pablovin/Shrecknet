"""Hard boundaries for traditional-agent graph retrieval."""

from __future__ import annotations

from collections.abc import Iterable


TRADITIONAL_AGENT_ALLOWED_LABELS = frozenset({
    "EntityInstance",
    "Scene",
    "Milestone",
})

TRADITIONAL_AGENT_EXCLUDED_LABELS = frozenset({
    "CharacterAgent",
    "CharacterAspect",
    "CharacterGoal",
})


def safe_retrieval_labels(labels: Iterable[str] | None = None) -> list[str]:
    """Return only labels traditional agents are permitted to retrieve."""
    requested = TRADITIONAL_AGENT_ALLOWED_LABELS if labels is None else set(labels)
    return sorted(requested & TRADITIONAL_AGENT_ALLOWED_LABELS)


def contains_excluded_label(labels: Iterable[str] | None) -> bool:
    return bool(set(labels or ()) & TRADITIONAL_AGENT_EXCLUDED_LABELS)
