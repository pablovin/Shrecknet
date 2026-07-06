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


class _FakeRetriever:
    async def list_entities_by_ontology(self, ontology_id: int, *, skip: int = 0, limit: int = 500):
        del ontology_id, skip, limit
        return [
            {"node_id": "e1", "alias": "Cwenhild", "ontology": "Character"},
            {"node_id": "e2", "alias": "Arthur", "ontology": "Character"},
            {"node_id": "e3", "alias": "Londinium", "ontology": "Location"},
        ]

    async def resolve_source_lineage(self, node_ids: list[str]):
        del node_ids
        return {}

    async def expand_timeline_context(
        self,
        *,
        query: str,
        ontology_ids: list[int],
        entity_scores: dict[str, float],
        max_scenes: int = 6,
        max_milestones: int = 6,
    ):
        del query, ontology_ids, entity_scores, max_scenes, max_milestones
        return []


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


@pytest.mark.asyncio
async def test_query_entity_matching_fuzzy_finds_typo_entities():
    orchestrator = ElderOrchestrator(
        llm_client=_FakeLLM(),
        model_policy=_FakeModelPolicy(),
        graph_retriever=_FakeRetriever(),
    )
    matches = await orchestrator._match_query_entities(
        query="what is the age of Cwenhild when she fought Artur in Londinium?",
        ontology_ids=[1],
    )
    ids = {m["node_id"] for m in matches}
    assert "e1" in ids
    assert "e2" in ids
    assert "e3" in ids


def test_memory_priors_include_query_entity_match_boost():
    orchestrator = ElderOrchestrator(
        llm_client=_FakeLLM(),
        model_policy=_FakeModelPolicy(),
        graph_retriever=SimpleNamespace(),
    )
    sources = [
        SimpleNamespace(
            node_id="e2",
            node_label="EntityInstance",
            node_name="Arthur",
            score=0.5,
            evidence_chunks=[SimpleNamespace(text="Arthur fought in Londinium", score=0.5)],
        )
    ]
    priors = orchestrator._apply_memory_priors(
        request_query="who is Arthur",
        sources=sources,  # type: ignore[arg-type]
        memory_summary={"recent_entities": [], "temporal_terms": [], "last_answer_terms": []},
        query_entity_ids={"e2"},
    )
    assert any(p.get("type") == "query_entity_match_prior" for p in priors)


@pytest.mark.asyncio
async def test_consolidate_sources_enriches_scene_and_milestone_lineage():
    retriever = _FakeRetriever()

    async def _resolve_source_lineage(node_ids: list[str]):
        assert set(node_ids) == {"scene-1", "milestone-1", "entity-1"}
        return {
            "scene-1": {
                "node_type": "scene",
                "scene_id": "scene-1",
                "source_entity_instance_id": "entity-1",
            },
            "milestone-1": {
                "node_type": "milestone",
                "scene_id": "scene-1",
                "source_entity_instance_id": "entity-1",
            },
        }

    retriever.resolve_source_lineage = _resolve_source_lineage  # type: ignore[attr-defined]
    orchestrator = ElderOrchestrator(
        llm_client=_FakeLLM(),
        model_policy=_FakeModelPolicy(),
        graph_retriever=retriever,
    )

    sources = await orchestrator._consolidate_sources(
        [
            {
                "chunks": [
                    RetrievedChunk(
                        node_id="scene-1",
                        node_label="Scene",
                        node_name="Council Meeting",
                        text="Scene text",
                        score=0.8,
                        confidence_pct=80.0,
                        properties={},
                    ),
                    RetrievedChunk(
                        node_id="milestone-1",
                        node_label="Milestone",
                        node_name="Oath Refused",
                        text="Milestone text",
                        score=0.7,
                        confidence_pct=70.0,
                        properties={},
                    ),
                    RetrievedChunk(
                        node_id="entity-1",
                        node_label="EntityInstance",
                        node_name="Aria",
                        text="Entity text",
                        score=0.9,
                        confidence_pct=90.0,
                        properties={},
                    ),
                ]
            }
        ]
    )

    by_id = {source.node_id: source for source in sources}
    assert by_id["scene-1"].node_type == "scene"
    assert by_id["scene-1"].scene_id == "scene-1"
    assert by_id["scene-1"].source_entity_instance_id == "entity-1"
    assert by_id["milestone-1"].node_type == "milestone"
    assert by_id["milestone-1"].scene_id == "scene-1"
    assert by_id["milestone-1"].source_entity_instance_id == "entity-1"
    assert by_id["entity-1"].node_type == "entityinstance"
    assert by_id["entity-1"].source_entity_instance_id is None


@pytest.mark.asyncio
async def test_timeline_expansion_merges_scene_and_milestone_chunks():
    retriever = _FakeRetriever()

    async def _expand_timeline_context(**kwargs):
        assert kwargs["entity_scores"] == {"entity-1": 0.9}
        return [
            RetrievedChunk(
                node_id="scene-1",
                node_label="Scene",
                node_name="Council Meeting",
                text="Scene: Council Meeting",
                score=0.86,
                confidence_pct=86.0,
                chunk_type="scene_main",
                properties={},
            ),
            RetrievedChunk(
                node_id="milestone-1",
                node_label="Milestone",
                node_name="Oath Refused",
                text="Milestone: Oath Refused",
                score=0.84,
                confidence_pct=84.0,
                chunk_type="milestone_main",
                properties={},
            ),
        ]

    retriever.expand_timeline_context = _expand_timeline_context  # type: ignore[attr-defined]
    orchestrator = ElderOrchestrator(
        llm_client=_FakeLLM(),
        model_policy=_FakeModelPolicy(),
        graph_retriever=retriever,
    )

    retrieval_results = [
        {
            "intent": DecomposedIntent(subquery="who is aria", target_data_type="mixed", reason="test"),
            "chunks": [
                RetrievedChunk(
                    node_id="entity-1",
                    node_label="EntityInstance",
                    node_name="Aria",
                    text="Aria entity",
                    score=0.9,
                    confidence_pct=90.0,
                    properties={},
                )
            ],
            "duration_ms": 1.0,
            "debug_stats": {},
        }
    ]

    expanded = await orchestrator._expand_timeline_candidates(
        query="Who is Aria?",
        retrieval_results=retrieval_results,
        ontology_ids=[1],
    )

    node_ids = [chunk.node_id for chunk in expanded[0]["chunks"]]
    assert node_ids[:3] == ["entity-1", "scene-1", "milestone-1"]
    assert expanded[0]["debug_stats"]["expanded_timeline_chunks"] == 2


def test_select_final_sources_reserves_timeline_slots():
    orchestrator = ElderOrchestrator(
        llm_client=_FakeLLM(),
        model_policy=_FakeModelPolicy(),
        graph_retriever=_FakeRetriever(),
    )
    sources = [
        SimpleNamespace(node_id="entity-1", node_label="EntityInstance", score=0.95),
        SimpleNamespace(node_id="entity-2", node_label="EntityInstance", score=0.92),
        SimpleNamespace(node_id="scene-1", node_label="Scene", score=0.7),
        SimpleNamespace(node_id="milestone-1", node_label="Milestone", score=0.69),
    ]

    selected = orchestrator._select_final_sources(sources, limit=3)  # type: ignore[arg-type]

    assert [source.node_id for source in selected] == ["scene-1", "milestone-1", "entity-1"]
