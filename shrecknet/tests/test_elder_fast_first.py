from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.jobs.elder.elder import ElderOrchestrator
from app.jobs.elder.schemas import DecomposedIntent, ElderQueryRequest, RetrievedChunk
from app.models.agent import Agent


class _FakeModelPolicy:
    model_elder = "test-elder-model"

    def get_model(self, _task):
        return "test-default-model"


class _FakeLLM:
    async def chat(self, **kwargs):
        del kwargs
        return "{}"


def _chunk(score: float, node_id: str = "n-1") -> RetrievedChunk:
    return RetrievedChunk(
        node_id=node_id,
        node_label="EntityInstance",
        node_name="Node",
        text="Some text",
        score=score,
        confidence_pct=score * 100.0,
        properties={},
    )


@pytest.mark.asyncio
async def test_fast_first_skips_decomposition_when_first_pass_is_strong(monkeypatch):
    orchestrator = ElderOrchestrator(
        llm_client=_FakeLLM(),
        model_policy=_FakeModelPolicy(),
        graph_retriever=SimpleNamespace(),
    )
    agent = Agent(id="agent-1", name="Elder", job="elder", active=True)
    agent.ontologies = [SimpleNamespace(id=2)]

    decompose_calls = {"count": 0}
    retrieve_calls = {"count": 0}

    async def _fake_decompose(*args, **kwargs):
        del args, kwargs
        decompose_calls["count"] += 1
        return [DecomposedIntent(subquery="q2", target_data_type="mixed", reason="x")]

    async def _fake_retrieve(*, intents, **kwargs):
        del intents, kwargs
        retrieve_calls["count"] += 1
        return [
            {
                "intent": DecomposedIntent(subquery="q", target_data_type="mixed", reason="r"),
                "chunks": [_chunk(0.9, "n-1"), _chunk(0.8, "n-2"), _chunk(0.75, "n-3")],
                "duration_ms": 1.0,
                "debug_stats": {},
            }
        ]

    async def _fake_synthesize(*args, **kwargs):
        del args, kwargs
        return "ok"

    monkeypatch.setattr(orchestrator, "_decompose", _fake_decompose)
    monkeypatch.setattr(orchestrator, "_retrieve_intents", _fake_retrieve)
    monkeypatch.setattr(orchestrator, "_synthesize", _fake_synthesize)

    response = await orchestrator.execute(
        agent=agent,
        request=ElderQueryRequest(query="test question", fast=False),
        chat_history=None,
    )

    assert response.answer == "ok"
    assert retrieve_calls["count"] == 1
    assert decompose_calls["count"] == 0


@pytest.mark.asyncio
async def test_fast_first_expands_to_decomposition_when_first_pass_is_weak(monkeypatch):
    orchestrator = ElderOrchestrator(
        llm_client=_FakeLLM(),
        model_policy=_FakeModelPolicy(),
        graph_retriever=SimpleNamespace(),
    )
    agent = Agent(id="agent-2", name="Elder", job="elder", active=True)
    agent.ontologies = [SimpleNamespace(id=2)]

    retrieve_call_intent_counts: list[int] = []
    decompose_calls = {"count": 0}

    async def _fake_decompose(*args, **kwargs):
        del args, kwargs
        decompose_calls["count"] += 1
        return [
            DecomposedIntent(subquery="q1", target_data_type="mixed", reason="a"),
            DecomposedIntent(subquery="q2", target_data_type="entity", reason="b"),
            DecomposedIntent(subquery="q3", target_data_type="scene", reason="c"),
            DecomposedIntent(subquery="q4", target_data_type="milestone", reason="d"),
        ]

    async def _fake_retrieve(*, intents, **kwargs):
        del kwargs
        retrieve_call_intent_counts.append(len(intents))
        if len(retrieve_call_intent_counts) == 1:
            return [
                {
                    "intent": intents[0],
                    "chunks": [_chunk(0.2, "n-low")],
                    "duration_ms": 1.0,
                    "debug_stats": {},
                }
            ]
        return [
            {
                "intent": intent,
                "chunks": [_chunk(0.8, f"n-{idx}")],
                "duration_ms": 1.0,
                "debug_stats": {},
            }
            for idx, intent in enumerate(intents, start=1)
        ]

    async def _fake_synthesize(*args, **kwargs):
        del args, kwargs
        return "ok"

    monkeypatch.setattr(orchestrator, "_decompose", _fake_decompose)
    monkeypatch.setattr(orchestrator, "_retrieve_intents", _fake_retrieve)
    monkeypatch.setattr(orchestrator, "_synthesize", _fake_synthesize)

    response = await orchestrator.execute(
        agent=agent,
        request=ElderQueryRequest(query="test question", fast=False),
        chat_history=None,
    )

    assert response.answer == "ok"
    assert retrieve_call_intent_counts[0] == 1
    assert retrieve_call_intent_counts[1] == 3
    assert decompose_calls["count"] == 1
