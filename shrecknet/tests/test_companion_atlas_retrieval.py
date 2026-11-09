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
        properties={"updated_at": "2026-01-01", "last_updated_date": "2026-02-01", "description": "A wolf-people of Glorantha."},
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
    assert result["evidence"][0]["reference"]["updated_at"] == "2026-02-01"
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


class _AtlasGraph:
    def __init__(self, nodes):
        self.nodes = nodes

    async def run(self, query, **params):
        # Evaluate the typed identity predicate against colliding graph records.
        label = next(label for label in ('EntityInstance', 'Scene', 'Milestone')
                     if f'MATCH (n:{label})' in query)
        assert "n['scene_id']" not in query and 'n.scene_id' not in query
        assert params['ontology'] == 6
        rows = []
        for node in self.nodes:
            if node['label'] != label or node['ontology_id'] != params['ontology']:
                continue
            identifier = (node.get('entity_instance_id') or node.get('id')) if label == 'EntityInstance' else node.get('id')
            if identifier != params['id']:
                continue
            revision = (node.get('last_updated_date') or node.get('updated_at')) if label == 'EntityInstance' else node.get('updated_at')
            rows.append({'revision': revision, 'image': node.get('node_avatar_url')})

        async def data():
            return rows[:2]
        return SimpleNamespace(data=data)


@pytest.mark.asyncio
@pytest.mark.parametrize('label', ['EntityInstance', 'Scene', 'Milestone'])
@pytest.mark.parametrize('state', ['unchanged', 'changed', 'deleted', 'duplicate'])
async def test_atlas_validation_checks_exact_typed_source(monkeypatch, label, state):
    nodes = [
        {'label':'EntityInstance', 'entity_instance_id':'same', 'ontology_id':6, 'last_updated_date':'entity'},
        {'label':'Scene', 'id':'same', 'ontology_id':6, 'updated_at':'scene'},
        {'label':'Milestone', 'id':'milestone', 'scene_id':'same', 'ontology_id':6, 'updated_at':'milestone'},
        {'label':'Milestone', 'id':'another', 'scene_id':'same', 'ontology_id':6, 'updated_at':'another'},
        {'label':label, 'id':'same', 'entity_instance_id':'same', 'ontology_id':99, 'updated_at':'other world'},
    ]
    selected = next(node for node in nodes if node['label'] == label)
    identifier = selected.get('entity_instance_id') or selected['id']
    revision = selected.get('last_updated_date') or selected.get('updated_at')
    if state == 'deleted':
        nodes.remove(selected)
    elif state == 'duplicate':
        nodes.append(dict(selected))
    reference = {'node_id':identifier, 'node_label':label,
                 'updated_at':'old' if state == 'changed' else revision, 'ontology_id':6}
    graph = _AtlasGraph(nodes)

    @asynccontextmanager
    async def session(**kwargs):
        yield graph

    monkeypatch.setattr(retrieval, 'get_driver', lambda: SimpleNamespace(session=session))
    adapter = retrieval.CompanionRetrieval(SimpleNamespace(request={'context':{'ontology_id':6}}), None)
    source = retrieval.evidence('atlas', identifier, 'Evidence', reference=reference, scope={'ontology_id':6})
    if state == 'unchanged':
        await adapter.validate_sources([source])
        from app.integrations.companion import media
        monkeypatch.setattr(media, 'get_driver', lambda: SimpleNamespace(session=session))
        assert (await media.atlas_image(reference, adapter.settings))['revision'] == revision
    else:
        with pytest.raises(ValueError, match='source_changed'):
            await adapter.validate_sources([source])
