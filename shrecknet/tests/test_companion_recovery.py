"""Strict recovery boundary, query privacy and need-aware evidence selection."""
import pytest
from app.jobs.companion.query_contracts import EvidenceAssessment, Need
from app.jobs.companion.recovery import web_calls
from app.jobs.companion.evidence_context import select_records, compact_evidence
from app.integrations.companion.retrieval import evidence


@pytest.mark.parametrize('payload',[
    {}, {'sufficient':'false','web_queries':[]}, {'sufficient':0,'web_queries':[]},
    {'sufficient':False,'web_queries':[' ']}, {'sufficient':False,'web_queries':[1]},
    {'sufficient':False,'web_queries':['a']*3}, {'sufficient':False,'web_queries':['x'*501]},
    {'sufficient':False,'web_queries':None}, {'sufficient':True,'web_queries':[],'needs':[]},
])
def test_assessment_rejects_invalid_contract(payload):
    with pytest.raises(ValueError):
        EvidenceAssessment.model_validate(payload)


def test_assessment_normalizes_queries_and_sufficient_discards_them():
    value=EvidenceAssessment(sufficient=False,web_queries=[' public  rule ', 'PUBLIC rule'])
    assert value.web_queries==['public  rule']
    assert EvidenceAssessment(sufficient=True,web_queries=['public rule']).web_queries==[]
    assert EvidenceAssessment(sufficient=False,web_queries=[]).web_queries==[]
    assert set(EvidenceAssessment.model_json_schema()['properties'])=={'sufficient','web_queries'}


def test_web_calls_cap_privacy_and_backend_ids():
    needs=[Need(id='n1',question='Private campaign'),Need(id='n2',question='Public rules')]
    value=EvidenceAssessment(sufficient=False,web_queries=['Secret campaign history','RuneQuest Telmori rules'])
    calls,blocked=web_calls(value,needs,limit=4,private_terms=['secret campaign'])
    assert len(calls)==1 and calls[0].query=='RuneQuest Telmori rules'
    assert calls[0].need_ids==['n1','n2'] and calls[0].id=='web-recovery-1'
    assert blocked==[{'reason':'private_identifier'}]
    assert not web_calls(value,needs,limit=0)[0]
    assert not web_calls(EvidenceAssessment(sufficient=True,web_queries=[]),needs,limit=2)[0]
    value=EvidenceAssessment(sufficient=False,web_queries=['public rules','public lore'])
    assert len(web_calls(value,needs,limit=1)[0])==1


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


from test_companion_query_pipeline import harness


@pytest.mark.asyncio
async def test_retry_can_recover_with_web_once_using_identical_context(harness):
    h=harness
    h.replies['companion.evidence_assessment']='not JSON'
    h.replies['companion.evidence_assessment_recovery']={'sufficient':False,'web_queries':['RuneQuest rules']}
    turn=await h.run()
    attempts=[data for tag,data in h.calls if tag.startswith('companion.evidence_assessment')]
    assert len(attempts)==2 and attempts[0]==attempts[1]
    assert [t for t,_,_ in h.retrievals]==['atlas','web']
    assert turn['result']['coverage']=='partial'
    assert turn['result']['diagnostics']['assessment_contract_version']=='sufficiency_v1'
    assert turn['result']['assessment']=={'sufficient':False,'web_queries':['RuneQuest rules']}


@pytest.mark.asyncio
async def test_internal_pool_survives_eight_excerpt_assessment_and_two_web_queries(harness,monkeypatch):
    from app.jobs.companion import query_pipeline as pipeline
    h=harness
    h.settings.shreckworld_base_url='http://rules'
    h.replies['companion.query_planner']={'tasks':[
        {'question':f'Goal {i}','sources':['atlas','shreckworld','character_memory']} for i in range(3)]}
    h.replies['companion.evidence_assessment']={'sufficient':False,'web_queries':['RuneQuest rules','RuneQuest lore']}
    old=pipeline.CompanionRetrieval
    class Retrieval(old):
        async def retrieve(self,tool,query,**kwargs):
            h.retrievals.append((tool,query,self.turn.execution_context.copy()))
            return {'tool':tool,'status':'ok','evidence':[
                evidence(tool,f'{query}-{i}','Fiona rules.',reference={'navigation_sentinel':'retained'},rank=i+1)
                for i in range(5)]}
    monkeypatch.setattr(pipeline,'CompanionRetrieval',Retrieval)
    turn=await h.run()
    result=turn['result']
    assert turn['status']=='completed',turn['error']
    assert len(result['evidence'])==20
    assert all(sum(r['source_type']==tool for r in result['evidence'])==5
               for tool in ('atlas','shreckworld','character_memory','web'))
    assessment=next(data for tag,data in h.calls if tag=='companion.evidence_assessment')
    synthesis=next(data for tag,data in h.calls if tag=='companion.grounded_synthesis')
    verification=next(data for tag,data in h.calls if tag=='companion.claim_verification')
    assert len(assessment['evidence'])==8 and len(synthesis['evidence'])==20
    assert all(any(need['id'] in row['need_ids'] for row in assessment['evidence']) for need in assessment['needs'])
    assert {row['source_type'] for row in assessment['evidence']}=={'atlas','shreckworld','character_memory'}
    assert verification['evidence']==synthesis['evidence']
    assert all(r['reference']['navigation_sentinel']=='retained' for r in result['evidence'])
    assert 'navigation_sentinel' not in str(h.calls)
    assert len([t for t,_,_ in h.retrievals if t=='web'])==2
    assert len([tag for tag,_ in h.calls if tag.startswith('companion.evidence_assessment')])==1
    assert result['coverage']=='partial'


@pytest.mark.asyncio
async def test_known_private_campaign_identifier_blocks_query_without_fallback(harness):
    h=harness
    h.replies['companion.evidence_assessment']={'sufficient':False,'web_queries':['First campaign secret']}
    turn=await h.run()
    assert all(t!='web' for t,_,_ in h.retrievals)
    assert turn['result']['diagnostics']['web_recovery_blocked']['reason']=='privacy_blocked'


@pytest.mark.asyncio
async def test_sufficient_decision_does_not_certify_uncertain_final_answer(harness):
    h=harness
    h.replies['companion.evidence_assessment']={'sufficient':True,'web_queries':['RuneQuest rules']}
    h.replies['companion.grounded_synthesis']={'segments':[{'id':'s1','kind':'uncertainty',
        'text':'The available sources leave the rule uncertain.','claims':[],'supports':[]}]}
    turn=await h.run()
    assert turn['result']['assessment']=={'sufficient':True,'web_queries':[]}
    assert turn['result']['coverage']=='partial'
    assert all(t!='web' for t,_,_ in h.retrievals)


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
