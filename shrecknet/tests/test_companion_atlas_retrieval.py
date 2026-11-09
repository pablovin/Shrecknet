"""Companion plans execute through Elder without redundant light-path planning."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.integrations.companion import retrieval


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['light', 'supplied', 'deep'])
async def test_atlas_reuses_plans_and_returns_evidence(monkeypatch, mode):
    from app.integrations.llm import shreckllm_client
    from app.jobs.elder import planner
    from app.services import elder_retrieval_service

    settings = SimpleNamespace(
        companion_tool_timeout_s=1,
        companion_web_timeout_s=1,
        neo4j_database="neo4j",
        shreckllm_base_url="http://shreckllm",
        model_elder_planner=SimpleNamespace(model_copy=lambda **_: None),
        model_agents_repair_json=SimpleNamespace(model_copy=lambda **_: None),
        companion_rich_response_active=False,
    )
    monkeypatch.setattr(retrieval, "get_settings", lambda: settings)
    monkeypatch.setattr(retrieval, "get_driver", lambda: SimpleNamespace(
        session=lambda **_: _Session()))

    from app.jobs.companion.query_contracts import PlannedCall
    planned = planner.single_query_plan('Tell me about the Telmori')
    planner_calls = []

    async def create_retrieval_plan(**kwargs):
        planner_calls.append(kwargs)
        return planned

    row = SimpleNamespace(
        node_id="node-1", node_label="EntityInstance", node_name="Telmori",
        node_alias=None, text="Telmori are a wolf-people of Glorantha.",
        properties={"updated_at": "2026-01-01", "description": "A wolf-people of Glorantha."},
    )

    async def retrieve_plan(*args, **kwargs):
        assert kwargs['plan'].steps[0].filters.ontology_ids == [6]
        assert kwargs['plan'].steps[0].query == 'Tell me about the Telmori'
        return ({"companion": [row]}, None, None)

    monkeypatch.setattr(planner, "create_retrieval_plan", create_retrieval_plan)
    monkeypatch.setattr(elder_retrieval_service, "retrieve_plan", retrieve_plan)
    monkeypatch.setattr(shreckllm_client, "ShreckLLMClient", _Client)

    adapter = retrieval.CompanionRetrieval(
        SimpleNamespace(request={"context": {"ontology_id": 6}}), None)
    call = PlannedCall(id='c1',tool='atlas',query='Tell me about the Telmori',need_ids=['n1'],
                       retrieval_depth='deep' if mode=='deep' else 'light',
                       depth_reason='Chronology' if mode=='deep' else None,
                       atlas_plan=planned if mode=='supplied' else None)
    result = await adapter.retrieve('atlas', call.query, planned_call=call)

    assert result["status"] == "ok"
    assert result["evidence"][0]["source_type"] == "atlas"
    assert result["evidence"][0]["source_id"] == "node-1"
    assert result["evidence"][0]["structured_evidence"]["description"] == "A wolf-people of Glorantha."
    assert len(planner_calls) == (1 if mode=='deep' else 0)
    if mode=='supplied':
        assert planned.steps[0].filters.ontology_ids == []  # Scoped copy, no mutation.


@pytest.mark.asyncio
async def test_supplied_atlas_plan_rejects_scope_expansion_without_replanning(monkeypatch):
    from app.jobs.companion.query_contracts import PlannedCall
    from app.jobs.elder import planner
    from app.services import elder_retrieval_service
    from app.integrations.retrieval import neo4j_retriever
    monkeypatch.setattr(neo4j_retriever,'HybridNeo4jGraphRetriever',lambda **kw:object())
    monkeypatch.setattr(retrieval,'get_settings',lambda: SimpleNamespace(
        companion_tool_timeout_s=600,neo4j_database='neo4j'))
    async def forbidden(*a,**kw):
        pytest.fail('A bad supplied plan must neither execute nor replan')
    monkeypatch.setattr(planner,'create_retrieval_plan',forbidden)
    monkeypatch.setattr(elder_retrieval_service,'retrieve_plan',forbidden)
    validations=[]
    validate=planner.validate_bounded_cypher
    def scoped(plan,ontology_ids):
        validations.append(ontology_ids)
        return validate(plan,ontology_ids)
    monkeypatch.setattr(planner,'validate_bounded_cypher',scoped)
    plan=planner.single_query_plan('Private world')
    plan.steps[0].filters.ontology_ids=[99]
    call=PlannedCall(id='c1',tool='atlas',query='Private world',need_ids=['n1'],atlas_plan=plan)
    adapter=retrieval.CompanionRetrieval(SimpleNamespace(request={'context':{'ontology_id':6}}),None)
    result=await adapter.retrieve('atlas',call.query,planned_call=call)
    assert result['status']=='unavailable'
    assert validations==[[6]]


class _Client:
    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Session:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, *args):
        return False
