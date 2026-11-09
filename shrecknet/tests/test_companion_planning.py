"""Research boundary, deterministic compilation and intent/recovery regressions."""
import pytest
from app.jobs.companion.query_contracts import SimpleCompanionPlan, EvidenceAssessment
from app.jobs.companion import planning, query_prompts
from test_companion_query_pipeline import harness


def proposal(tasks):
    return SimpleCompanionPlan(mode='search',tasks=tasks)


def test_tiny_schema_rejects_execution_plumbing_and_inconsistent_modes():
    schema = str(SimpleCompanionPlan.model_json_schema())
    assert 'RetrievalPlan' not in schema and 'atlas_plan' not in schema
    assert 'atlas_plan' not in query_prompts.PLANNER and 'need_ids' not in query_prompts.PLANNER
    assert 'atlas_plan' not in query_prompts.ASSESS
    assert 'retrieval_depth' not in str(EvidenceAssessment.model_json_schema())
    for payload in ({'mode':'search','tasks':[]}, {'mode':'chat','tasks':[{'question':'x','sources':['atlas']}]},
                    {'mode':'chat','tasks':[],'ontology_update':None},
                    {'tasks':[{'question':' ','sources':['atlas']}]},
                    {'tasks':[{'question':'x','sources':['atlas'],'retrieval_depth':'deep'}]}):
        with pytest.raises(ValueError): SimpleCompanionPlan.model_validate(payload)


def test_compiler_assigns_nine_light_calls_and_merges_duplicates():
    tasks=[{'question':f'Question {i}','sources':['shreckworld','atlas','chronicle']} for i in range(3)]
    plan, trace=planning.compile_plan(proposal(tasks),['shreckworld','atlas','chronicle'])
    assert [c.id for c in plan.calls]==[f'c{i}' for i in range(1,10)]
    assert [n.id for n in plan.needs]==['n1','n2','n3']
    assert all(c.retrieval_depth=='light' and c.atlas_plan is None for c in plan.calls)
    plan,trace=planning.compile_plan(proposal([tasks[0],tasks[0]]),['atlas'])
    assert len(plan.needs)==len(plan.calls)==1 and trace['merged_tasks']==1
    assert all(c.tool=='atlas' for c in plan.calls)


def test_memory_gate_and_web_recovery_only_guard():
    from types import SimpleNamespace
    tasks=[SimpleNamespace(question='private detail',sources=['character_memory','web'])]
    untrusted=SimpleNamespace(mode='search',tasks=tasks)
    plan,trace=planning.compile_plan(untrusted,['character_memory','web','atlas'])
    assert [c.tool for c in plan.calls]==['atlas']
    assert {r['reason'] for r in trace['rejected_sources']}=={'memory_not_relevant','web_recovery_only'}
    plan,_=planning.compile_plan(untrusted,['character_memory','web'],memory_relevant=True)
    assert [c.query for c in plan.calls]==['private detail'] and plan.calls[0].memory_reason
    with pytest.raises(ValueError):
        proposal([{'question':'Public fact','sources':['web']}])


def test_long_fallback_preserves_goal_and_segments_without_loss():
    question='a'*3999+'b'*4001
    plan,trace=planning.fallback_plan(question,['atlas','character_memory'])
    assert plan.needs[0].question==question
    assert ''.join(c.query for c in plan.calls)==question
    assert all(c.tool=='atlas' for c in plan.calls) and trace['deferred_segments']==0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['{}',TimeoutError('offline')])
async def test_technical_planner_failure_searches_original_goal(harness,failure):
    h=harness;h.replies['companion.query_planner']=failure
    result=await h.run('Original complete goal')
    assert result['result']['outcome']=='answered'
    assert h.retrievals[0][1]=='Original complete goal'
    assert result['result']['diagnostics']['planning']['fallback_used']
    assert not any(tag=='companion.clarification' for tag,_ in h.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('decision',[{'relevant':False},RuntimeError('offline')])
async def test_memory_gate_uses_original_intent_and_fails_closed(harness,decision):
    h=harness
    h.replies['companion.query_planner']={'tasks':[{'question':'Character memories','sources':['character_memory']}]}
    h.replies['companion.memory_relevance']=decision
    result=await h.run('Explain a generic rule')
    assert result['status']=='completed'
    assert all(row[0]!='character_memory' for row in h.retrievals)
    gate=next(data for tag,data in h.calls if tag=='companion.memory_relevance')
    assert gate['query']=='Explain a generic rule'


@pytest.mark.asyncio
async def test_initial_web_choice_falls_back_to_internal_retrieval(harness):
    h=harness
    h.replies['companion.query_planner']={'tasks':[{'question':'Private campaign rule','sources':['web']}]}
    result=await h.run()
    assert result['status']=='completed'
    assert all(t!='web' and q=='Who rules?' for t,q,_ in h.retrievals)
    assert any(t=='atlas' for t,_,_ in h.retrievals)
    assert result['result']['diagnostics']['planning']['fallback_used']
    assert not any(tag=='companion.public_recovery_query' for tag,_ in h.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('resume',[False,True])
async def test_pending_goal_is_only_resumed_for_relevant_continuation(harness,resume):
    from app.models.character_companion import CompanionChat
    h=harness
    async with h.sessions() as session:
        chat=await session.get(CompanionChat,h.cid)
        chat.pending_clarification={'id':'pending','query':'Old research goal','questions':['Which detail?'],
                                    'kind':'general','target_id':None}
        await session.commit()
    h.replies['companion.scope_intent']={'action':'keep','ontology_name':None,'resume_pending':resume}
    result=await h.run('New message')
    assert result['status']=='completed'
    data=next(data for tag,data in h.calls if tag=='companion.query_planner')
    assert data['query']==('Old research goal\nClarification: New message' if resume else 'New message')


@pytest.mark.asyncio
async def test_evidence_budget_is_shared_across_needs_not_completion_speed(harness,monkeypatch):
    from app.jobs.companion import query_pipeline as pipeline
    from app.integrations.companion.retrieval import evidence
    h=harness
    h.replies['companion.query_planner']={'tasks':[
        {'question':f'Goal {i}','sources':['atlas']} for i in range(3)]}
    old=pipeline.CompanionRetrieval
    class Retrieval(old):
        async def retrieve(self,tool,query,**kwargs):
            return {'tool':tool,'status':'ok','evidence':[
                evidence(tool,f'{query}-{i}','Fiona rules.',scope={'ontology_id':1}) for i in range(20)]}
    monkeypatch.setattr(pipeline,'CompanionRetrieval',Retrieval)
    h.replies['companion.evidence_assessment']={'sufficient':True,'web_queries':[]}
    result=await h.run()
    assert result['status']=='completed'
    records=next(data['evidence'] for tag,data in h.calls if tag=='companion.evidence_assessment')
    counts=[sum(need in row['need_ids'] for row in records) for need in ('n1','n2','n3')]
    assert len(records)==5 and max(counts)-min(counts)<=1
    omissions=result['result']['diagnostics']['evidence_omissions']
    assert omissions and all(row['reason']=='record_limit' for row in omissions)


@pytest.mark.asyncio
async def test_invalid_planning_without_local_permission_uses_only_safe_web(harness):
    h=harness
    h.replies['companion.query_planner']='{}'
    h.replies['companion.evidence_assessment']={'sufficient':False,'web_queries':['RuneQuest public rules']}
    result=await h.run('Private campaign rule question',tools={'allowed':[]})
    assert result['result']['outcome']=='answered'
    assert [(tool,query) for tool,query,_ in h.retrievals]==[('web','RuneQuest public rules')]
    assert result['result']['diagnostics']['planning']['fallback_used']
