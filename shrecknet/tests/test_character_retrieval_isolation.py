from __future__ import annotations

import pytest

from app.graphrag.retrieval_policy import (
    TRADITIONAL_AGENT_ALLOWED_LABELS,
    TRADITIONAL_AGENT_EXCLUDED_LABELS,
    contains_excluded_label,
    safe_retrieval_labels,
)
from app.graphrag.retrieval_service import RetrievalService


@pytest.mark.parametrize("label", sorted(TRADITIONAL_AGENT_EXCLUDED_LABELS))
def test_character_labels_are_never_allowed_for_traditional_retrieval(label: str):
    assert safe_retrieval_labels([label]) == []
    assert contains_excluded_label([label])


def test_mixed_caller_labels_drop_character_nodes():
    requested = [
        "EntityInstance",
        "CharacterAgent",
        "Scene",
        "CharacterAspect",
        "Milestone",
        "CharacterGoal",
    ]
    assert set(safe_retrieval_labels(requested)) == TRADITIONAL_AGENT_ALLOWED_LABELS
    assert contains_excluded_label(requested)


def test_unknown_future_labels_are_default_denied():
    assert safe_retrieval_labels(["EntityInstance", "UnreviewedAgentMemory"]) == [
        "EntityInstance"
    ]


def test_default_policy_is_an_explicit_allowlist():
    assert set(safe_retrieval_labels()) == {
        "EntityInstance",
        "Scene",
        "Milestone",
    }


class _NoQuerySession:
    async def run(self, *_args, **_kwargs):
        raise AssertionError("blocked labels must not execute Neo4j queries")


@pytest.mark.asyncio
async def test_blocked_only_request_returns_before_embedding_or_graph_work():
    result = await RetrievalService(_NoQuerySession()).semantic_search(
        query="secret character background",
        ontology_id=42,
        allowed_labels=["CharacterAgent", "CharacterGoal"],
    )
    assert result["results"] == []
    assert result["debug_stats"]["retrieval_mode"] == "blocked_by_label_policy"
