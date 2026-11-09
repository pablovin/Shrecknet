"""Bounded evidence selection and adapter diagnostics."""
import pytest
from app.jobs.companion.query_contracts import Need
from app.jobs.companion.evidence_context import compact_evidence, select_records
from app.integrations.companion.retrieval import evidence


def record(tool, identifier, text, need):
    return compact_evidence(evidence(tool,identifier,text),[need])


def test_selection_reserves_each_need_and_source_diversity_independent_of_order():
    needs=[Need(id='n1',question='Telmori traits'),Need(id='n2',question='Creation sequence')]
    rows=[record('atlas',str(i),'Telmori traits','n1') for i in range(5)]
    rows += [record('shreckworld','book','Creation sequence','n2')]
    rows += [record('character_memory',str(i),'Telmori traits','n1') for i in range(5)]
    selected,omissions=select_records(rows,needs,limit=8,token_budget=10000)
    reverse,_=select_records(list(reversed(rows)),needs,limit=8,token_budget=10000)
    assert selected==reverse and len(selected)==8 and len(omissions)==3
    assert any('n2' in row['need_ids'] for row in selected)
    assert {row['source_type'] for row in selected}=={'atlas','shreckworld','character_memory'}


def test_selector_skips_oversized_record_without_mutating_sources():
    needs=[Need(id='n1',question='Telmori traits')]
    large=record('atlas','large','Telmori traits '*1000,'n1')
    small=record('shreckworld','small','Telmori traits','n1')
    selected,omissions=select_records([large,small],needs,limit=8,token_budget=500)
    assert selected==[small] and omissions==[{'evidence_id':large['evidence_id'],'reason':'evidence_token_budget'}]
    assert len(large['excerpt'])==8000


@pytest.mark.asyncio
async def test_adapter_caps_returned_records_and_records_omissions(monkeypatch):
    from types import SimpleNamespace
    from app.integrations.companion import retrieval
    monkeypatch.setattr(retrieval,'get_settings',lambda:SimpleNamespace(companion_tool_timeout_s=1))
    adapter=retrieval.CompanionRetrieval(SimpleNamespace(request={'context':{}}),None)
    rows=[evidence('atlas',str(i),'A passage.') for i in range(8)]
    async def atlas(query,**kwargs): return rows
    monkeypatch.setattr(adapter,'atlas',atlas)
    result=await adapter.retrieve('atlas','a question')
    assert result['evidence']==rows[:5]
    assert len(result['evidence_omissions'])==3
