"""ShreckWorld compatibility diagnostics must survive retrieval and answer history."""
from types import SimpleNamespace

import httpx
import pytest

from app.integrations.companion import retrieval
from app.jobs.companion.contracts import build_answer
from app.schemas.character_companion import AnswerDetailsRead


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "coverage, citations, status, reason",
    [
        ({"requested_source_count": 1, "searched_source_count": 1, "sources_without_ready_index": 0, "sources_requiring_reindex": 0}, [], "empty", None),
        ({"requested_source_count": 1, "searched_source_count": 0, "sources_without_ready_index": 0, "sources_requiring_reindex": 1}, [], "empty", "incompatible_index"),
        ({"requested_source_count": 2, "searched_source_count": 1, "sources_without_ready_index": 0, "sources_requiring_reindex": 1}, [{"library_item_id": "book", "excerpt": "A rule."}], "ok", "incompatible_index"),
        ({"requested_source_count": 1, "searched_source_count": 0, "sources_without_ready_index": 1, "sources_requiring_reindex": 0}, [], "empty", "index_not_ready"),
    ],
)
async def test_rules_source_coverage_is_not_mistaken_for_empty(monkeypatch, coverage, citations, status, reason):
    settings = SimpleNamespace(companion_tool_timeout_s=1, shreckworld_base_url="http://shreckworld")
    monkeypatch.setattr(retrieval, "get_settings", lambda: settings)
    monkeypatch.setattr(retrieval, "grant", lambda *args: "test-grant")
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"citations": citations, "source_coverage": coverage}))
    monkeypatch.setattr(retrieval.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    adapter = retrieval.CompanionRetrieval(SimpleNamespace(request={"context": {"ontology_id": 7}}), None)

    outcome = await adapter.retrieve("shreckworld", "What is the rule?")
    assert outcome["status"] == status
    assert outcome.get("reason") == reason
    assert outcome["source_coverage"] == coverage
    assert outcome["source_readiness"] == ("partial" if reason else "ready")
    assert len(outcome["evidence"]) == len(citations)
    answer = build_answer("I can only use the available sources.", outcome["evidence"], [outcome], query="What is the rule?")
    details = AnswerDetailsRead.model_validate(answer)
    assert details.tool_calls[0].reason == reason
    assert details.tool_calls[0].source_coverage == coverage
    if reason:
        assert answer["coverage"] == "partial"
        assert reason in answer["warnings"][0]


@pytest.mark.asyncio
async def test_missing_coverage_is_unavailable_not_empty(monkeypatch):
    settings = SimpleNamespace(companion_tool_timeout_s=1, shreckworld_base_url="http://shreckworld")
    monkeypatch.setattr(retrieval, "get_settings", lambda: settings)
    monkeypatch.setattr(retrieval, "grant", lambda *args: "test-grant")
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"citations": []}))
    monkeypatch.setattr(retrieval.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    adapter = retrieval.CompanionRetrieval(SimpleNamespace(request={"context": {"ontology_id": 7}}), None)
    assert (await adapter.retrieve("shreckworld", "rule"))["status"] == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize('oversized',[False,True])
async def test_planned_rules_keep_child_coverage_and_never_truncate_rule_units(monkeypatch,oversized):
    from app.core.config_store import Settings
    settings=Settings(companion_query_pipeline_version='evidence_v2',shreckworld_base_url='http://rules')
    monkeypatch.setattr(retrieval,'get_settings',lambda:settings)
    monkeypatch.setattr(retrieval,'grant',lambda *a:'grant')
    original=httpx.AsyncClient
    def response(request):
        import json
        assert json.loads(request.content)['retrieval_strategy']=='planned'
        return httpx.Response(200,json={
            'citations':[{'source_id':'source-1','library_item_id':'book','chunk_id':'chunk','excerpt':'Preview.', 'evidence_text':'x'*8001 if oversized else 'Complete rule, including exceptions.'}],
            'source_coverage':{'requested_source_count':1,'searched_source_count':1,'sources_without_ready_index':0,'sources_requiring_reindex':0},
            'evidence_coverage':{'status':'partial','needs':[]},'v3_trace':{'followup_rounds':1},
            'evidence_groups':[{'evidence_id':'source-1','need_ids':['rule'],'chunk_ids':['chunk']}],
        })
    transport=httpx.MockTransport(response)
    monkeypatch.setattr(retrieval.httpx,'AsyncClient',lambda **kw:original(transport=transport,**kw))
    adapter=retrieval.CompanionRetrieval(SimpleNamespace(request={'context':{'ontology_id':1}}),None)
    from app.jobs.companion.query_contracts import PlannedCall
    call=PlannedCall(id='rules',tool='shreckworld',query='Explain the rule',need_ids=['rule'],retrieval_depth='deep',depth_reason='Interacting exceptions')
    result=await adapter.retrieve('shreckworld',call.query,planned_call=call)
    assert result['source_trace']['v3_trace']['followup_rounds']==1
    if oversized:
        assert result['evidence']==[] and result['source_trace']['omitted_oversized_units']==1
    else:
        assert result['evidence'][0]['excerpt']=='Complete rule, including exceptions.'
        assert result['evidence'][0]['structured_evidence']['evidence_groups'][0]['need_ids']==['rule']


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False,True])
async def test_light_rules_use_scoped_single_evidence_without_child_llm(monkeypatch,legacy):
    from app.core.config_store import Settings
    settings=Settings(shreckworld_base_url='http://rules')
    monkeypatch.setattr(retrieval,'get_settings',lambda:settings)
    monkeypatch.setattr(retrieval,'grant',lambda *a:'grant')
    original=httpx.AsyncClient
    def response(request):
        import json
        payload=json.loads(request.content)
        assert payload=={'query':'Explain a single rule','mode':'evidence','retrieval_strategy':'single',
                         'top_k':5,'library_item_ids':['book']}
        assert request.extensions['timeout']['read']==600
        assert request.url.path==('/shreckworlds/world/companion-evidence' if legacy else '/ontologies/7/companion-evidence')
        return httpx.Response(200,json={'citations':[{'library_item_id':'book','chunk_id':'chunk',
            'excerpt':'Rule excerpt.','source_sha256':'a'*64,'page_number':4}],
            'source_coverage':{'requested_source_count':1,'searched_source_count':1,
                'sources_without_ready_index':0,'sources_requiring_reindex':0}})
    monkeypatch.setattr(retrieval.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(response),**kw))
    context={'ontology_id':7,'library_item_ids':['book']}
    if legacy:
        context['shreckworld_id']='world'
    adapter=retrieval.CompanionRetrieval(SimpleNamespace(request={'context':context}),None)
    result=await adapter.retrieve('shreckworld','Explain a single rule')
    assert result['status']=='ok' and 'source_trace' not in result
    assert result['evidence'][0]['reference']['page_number']==4
    assert result['evidence'][0]['structured_evidence'] is None
    assert result['timeout_s']==600


@pytest.mark.asyncio
@pytest.mark.parametrize('layer',['http','tool'])
async def test_retrieval_timeouts_cancel_work_and_report_the_effective_layer(monkeypatch,layer):
    import asyncio
    settings=SimpleNamespace(companion_tool_timeout_s=.01)
    monkeypatch.setattr(retrieval,'get_settings',lambda:settings)
    adapter=retrieval.CompanionRetrieval(SimpleNamespace(request={'context':{}}),None)
    cancelled=[]
    async def source(query):
        if layer=='http':
            raise httpx.ReadTimeout('slow upstream')
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    monkeypatch.setattr(adapter,'chronicle',source)
    result=await adapter.retrieve('chronicle','question')
    assert result['status']=='timed_out' and result['timeout_layer']==layer
    assert bool(cancelled)==(layer=='tool')
    assert not result['evidence']


@pytest.mark.asyncio
async def test_partial_index_evidence_retains_attention_details_and_answer_breakdown(monkeypatch):
    coverage={'requested_source_count':3,'searched_source_count':1,
              'sources_without_ready_index':1,'sources_requiring_reindex':1}
    attention=[{'library_item_id':'unindexed','title':'Supplement','reason':'index_not_ready'},
               {'library_item_id':'old','title':'Old Rules','reason':'incompatible_index'}]
    monkeypatch.setattr(retrieval,'get_settings',lambda:SimpleNamespace(
        companion_tool_timeout_s=600,shreckworld_base_url='http://rules'))
    monkeypatch.setattr(retrieval,'grant',lambda *args:'grant')
    original=httpx.AsyncClient
    transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
        'citations':[{'library_item_id':'ready','title':'Core Rules','excerpt':'A usable rule.','page_number':12}],
        'source_coverage':coverage,'sources_requiring_attention':attention}))
    monkeypatch.setattr(retrieval.httpx,'AsyncClient',lambda **kw:original(transport=transport,**kw))
    adapter=retrieval.CompanionRetrieval(SimpleNamespace(request={'context':{'ontology_id':7}}),None)
    outcome=await adapter.retrieve('shreckworld','A rule')
    assert outcome['status']=='ok' and outcome['source_readiness']=='partial'
    assert outcome['source_coverage']==coverage and outcome['sources_requiring_attention']==attention
    answer=build_answer('A usable rule.[[source:1]]',outcome['evidence'],[outcome])
    parsed=AnswerDetailsRead.model_validate(answer)
    assert parsed.coverage=='partial'
    assert [row.model_dump() for row in parsed.tool_calls[0].sources_requiring_attention]==attention
    assert answer['citations'][0]['reference']['library_item_id']=='ready'
    assert any('Supplement (unindexed)' in warning for warning in parsed.warnings)


@pytest.mark.asyncio
async def test_genuine_service_failure_is_distinct_from_partial_indexes(monkeypatch):
    monkeypatch.setattr(retrieval,'get_settings',lambda:SimpleNamespace(
        companion_tool_timeout_s=600,shreckworld_base_url='http://rules'))
    monkeypatch.setattr(retrieval,'grant',lambda *args:'grant')
    original=httpx.AsyncClient
    transport=httpx.MockTransport(lambda request:httpx.Response(503,json={'detail':'offline'}))
    monkeypatch.setattr(retrieval.httpx,'AsyncClient',lambda **kw:original(transport=transport,**kw))
    adapter=retrieval.CompanionRetrieval(SimpleNamespace(request={'context':{'ontology_id':7}}),None)
    result=await adapter.retrieve('shreckworld','A rule')
    assert result['status']=='unavailable' and not result['evidence']
    assert 'source_coverage' not in result
