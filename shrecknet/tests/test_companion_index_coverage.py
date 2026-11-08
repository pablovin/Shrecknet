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
        ({"requested_source_count": 1, "searched_source_count": 0, "sources_without_ready_index": 0, "sources_requiring_reindex": 1}, [], "unavailable", "incompatible_index"),
        ({"requested_source_count": 2, "searched_source_count": 1, "sources_without_ready_index": 0, "sources_requiring_reindex": 1}, [{"library_item_id": "book", "excerpt": "A rule."}], "unavailable", "incompatible_index"),
        ({"requested_source_count": 1, "searched_source_count": 0, "sources_without_ready_index": 1, "sources_requiring_reindex": 0}, [], "unavailable", "index_not_ready"),
    ],
)
async def test_rules_source_coverage_is_not_mistaken_for_empty(monkeypatch, coverage, citations, status, reason):
    settings = SimpleNamespace(companion_tool_timeout_s=1, companion_shreckworld_url="http://shreckworld")
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
    settings = SimpleNamespace(companion_tool_timeout_s=1, companion_shreckworld_url="http://shreckworld")
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
    settings=Settings(companion_query_pipeline_version='evidence_v2',companion_shreckworld_url='http://rules')
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
    result=await adapter.retrieve('shreckworld','Explain the rule')
    assert result['source_trace']['v3_trace']['followup_rounds']==1
    if oversized:
        assert result['evidence']==[] and result['source_trace']['omitted_oversized_units']==1
    else:
        assert result['evidence'][0]['excerpt']=='Complete rule, including exceptions.'
        assert result['evidence'][0]['structured_evidence']['evidence_groups'][0]['need_ids']==['rule']
