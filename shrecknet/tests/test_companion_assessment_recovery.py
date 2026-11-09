"""Semantic-review outages retain attributed evidence without promoting claims."""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.jobs.companion import query_pipeline as pipeline
from app.jobs.companion.evidence_context import compact_evidence, compact_outcomes, recovery_records, extractive_response
from app.integrations.companion.retrieval import evidence
from test_companion_query_pipeline import harness, plan


def book(identifier='book:chunk', text='Fiona rules.'):
    return evidence('shreckworld',identifier,text,title='Core Rules',
        reference={'library_item_id':'book','document_id':'doc','source_sha256':'a'*64,
                   'page_number':12,'page_label':'iv','content_url':'/private/book#page=12',
                   'geometry':{'large_navigation_blob':'NAVIGATION_SENTINEL'*2000}},
        scope={'ontology_id':1})


def install_books(monkeypatch, rows):
    old=pipeline.CompanionRetrieval
    class Retrieval(old):
        async def retrieve(self,tool,query,**kwargs):
            return {'tool':tool,'status':'ok','evidence':rows}
    monkeypatch.setattr(pipeline,'CompanionRetrieval',Retrieval)


def supported(data):
    row=data['evidence'][0]
    return {'needs':[{'need_id':'n1','status':'supported','supports':[
        {'evidence_id':row['evidence_id'],'quote':'Fiona rules.'}],
        'conflicting_evidence_ids':[],'missing':''}],'follow_up_calls':[]}


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[TimeoutError(), '{}', 'not JSON', RuntimeError('provider secret must not leak')])
async def test_failed_review_quotes_retained_book_with_diagnostics_and_no_synthesis(harness,monkeypatch,failure):
    h=harness
    source=book()
    install_books(monkeypatch,[source])
    h.replies['companion.evidence_assessment']=failure
    h.replies['companion.evidence_assessment_recovery']=failure
    turn=await h.run()
    assert turn['status']=='completed',turn['error']
    result=turn['result']
    assert result['outcome']=='limited' and result['coverage']=='partial'
    assert 'Fiona rules.' in result['text'] and 'not a verified answer' in result['text']
    assert result['evidence']==[source] and h.validated==[source]
    assert result['citations'][0]['reference']==source['reference']
    assert all(segment['kind'] in {'uncertainty','extractive'} and not segment['claims'] for segment in result['segments'])
    assert result['citations'][0]['segment_ids']==['excerpt-1']
    assert not any(tag=='companion.grounded_synthesis' for tag,_ in h.calls)
    trace=result['diagnostics']['evidence_assessment']
    assert trace['fallback_mode']=='extractive' and trace['fallback_used']
    assert trace['retained_evidence_count']==1 and trace['sources_retained_for_extraction']==1
    assert trace['sources_retained_for_synthesis']==0
    assert len(trace['attempts'])==2
    for entry in trace['attempts']:
        assert entry['error_type'] and entry['elapsed_ms']>=0
        assert entry['started_at']<=entry['ended_at']
        assert entry['input_evidence_count']==1 and entry['compact_prompt_tokens']>0
        assert entry['model']['name']==h.settings.model_companion_evidence.name
        assert entry['configured_deadline_s']==600
        assert entry['compact_prompt_bytes']>=entry['compact_prompt_characters']
    assert trace['attempts'][1]['effective_deadline_s']==60
    assert trace['attempts'][1]['compact_prompt_bytes']<trace['attempts'][0]['compact_prompt_bytes']
    assert turn['metrics']['evidence_assessment']==trace
    assert 'provider secret' not in json.dumps(trace)
    assert 'NAVIGATION_SENTINEL' not in json.dumps(h.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[TimeoutError(), '{}', 'invalid_support'])
async def test_compact_retry_restores_normal_synthesis_and_verification(harness,monkeypatch,failure):
    h=harness
    source=book()
    install_books(monkeypatch,[source])
    if failure=='invalid_support':
        value=supported({'evidence':[source]})
        value['needs'][0]['supports'][0]['quote']='An invented quote.'
        h.replies['companion.evidence_assessment']=value
    else:
        h.replies['companion.evidence_assessment']=failure
    h.replies['companion.evidence_assessment_recovery']=lambda tag,data:supported(data)
    h.replies['companion.claim_verification']=lambda tag,data:{'segments':[
        {'segment_id':segment['id'],'supported':True,'reason':'Exact source support'}
        for segment in data['draft']['segments']]}
    async def no_media(*args): return {}
    monkeypatch.setattr(pipeline.companion_media,'resolve',no_media)
    turn=await h.run()
    assert turn['status']=='completed',turn['error']
    result=turn['result']
    assert result['outcome']=='answered' and result['coverage']=='complete'
    assert result['diagnostics']['evidence_assessment']['fallback_mode']=='compact_retry'
    assert not result['diagnostics'].get('extractive_fallback')
    assert any(tag=='companion.grounded_synthesis' for tag,_ in h.calls)
    assert any(tag=='companion.claim_verification' for tag,_ in h.calls)
    assert result['citations'][0]['reference']['source_sha256']=='a'*64
    for tag,data in h.calls:
        if tag in {'companion.evidence_assessment','companion.evidence_assessment_recovery',
                   'companion.grounded_synthesis','companion.claim_verification'}:
            assert data['evidence'][0]['excerpt']==source['excerpt']
            assert data['evidence'][0]['page_number']==12
            assert data['evidence'][0]['need_ids']==['n1']
            assert 'reference' not in data['evidence'][0] and 'navigation' not in data['evidence'][0]
    if failure=='invalid_support':
        assert result['diagnostics']['evidence_assessment']['attempts'][0]['validation_failures']==[
            {'type':'invalid_assessment_support'}]


@pytest.mark.asyncio
async def test_retry_omissions_do_not_remove_full_evidence_or_its_navigation(harness,monkeypatch):
    h=harness
    rows=[book(f'book:{i}','Fiona rules. '+('A complete passage. '*100)) for i in range(6)]
    install_books(monkeypatch,rows)
    h.replies['companion.evidence_assessment']=TimeoutError()
    h.replies['companion.evidence_assessment_recovery']='{}'
    turn=await h.run()
    assert turn['status']=='completed',turn['error']
    result=turn['result']
    assert result['evidence']==rows
    retry=next(data for tag,data in h.calls if tag=='companion.evidence_assessment_recovery')
    assert 0<len(retry['evidence'])<=4
    assert all(row['excerpt']==next(source['excerpt'] for source in rows if source['evidence_id']==row['evidence_id']) for row in retry['evidence'])
    assert any(row['reason'].startswith('assessment_retry_') for row in result['diagnostics']['evidence_omissions'])
    assert all(row['reference']['geometry'] for row in result['evidence'])
    assert len([tag for tag,_ in h.calls if tag=='companion.evidence_assessment_recovery'])==1


@pytest.mark.asyncio
@pytest.mark.parametrize('verifier_fails',[False,True])
async def test_failed_reassessment_reuses_previous_support_but_requires_final_verification(harness,verifier_fails):
    h=harness
    count=0
    def assess(tag,data):
        nonlocal count
        count+=1
        if count>1:
            raise TimeoutError()
        result=supported(data)
        result['needs'][0].update(status='partial',missing='More details')
        result['follow_up_calls']=[{'id':'r1','tool':'atlas','query':'Additional details','need_ids':['n1']}]
        return result
    h.replies['companion.evidence_assessment']=assess
    h.replies['companion.evidence_assessment_recovery']=TimeoutError()
    if verifier_fails:
        h.replies['companion.claim_verification']=RuntimeError('Verifier offline')
    turn=await h.run()
    assert turn['status']=='completed',turn['error']
    assert turn['result']['coverage']=='partial'
    assert turn['result']['diagnostics']['evidence_assessment']['reused_previous_assessment']
    assert any(tag=='companion.grounded_synthesis' for tag,_ in h.calls)
    assert any(tag=='companion.claim_verification' for tag,_ in h.calls)
    assert turn['result']['diagnostics'].get('extractive_fallback',False)==verifier_fails


@pytest.mark.asyncio
async def test_no_evidence_never_becomes_an_uncited_factual_fallback(harness,monkeypatch):
    h=harness
    install_books(monkeypatch,[])
    h.replies['companion.evidence_assessment']=TimeoutError()
    turn=await h.run()
    assert turn['status']=='completed',turn['error']
    result=turn['result']
    assert not result['evidence'] and not result['citations']
    assert not any(segment['kind']=='factual' for segment in result['segments'])
    assert not any(tag=='companion.evidence_assessment_recovery' for tag,_ in h.calls)


@pytest.mark.asyncio
async def test_extracts_still_fail_closed_on_source_revocation(harness,monkeypatch):
    h=harness
    install_books(monkeypatch,[book()])
    h.replies['companion.evidence_assessment']=TimeoutError()
    h.replies['companion.evidence_assessment_recovery']=TimeoutError()
    checks=[]
    async def denied(self,rows):
        checks.append(rows)
        raise HTTPException(403,'Source access revoked')
    monkeypatch.setattr(pipeline.CompanionRetrieval,'validate_sources',denied)
    turn=await h.run()
    assert turn['status']=='failed' and turn['result'] is None
    assert len(checks)==1


@pytest.mark.asyncio
async def test_collection_budget_is_unchanged_and_records_omission_reason(harness,monkeypatch):
    h=harness
    h.settings.companion_max_evidence_tokens=100
    source=book(text='Long passage. '*300)
    install_books(monkeypatch,[source])
    h.replies['companion.evidence_assessment']=TimeoutError()
    turn=await h.run()
    assert turn['status']=='completed',turn['error']
    assert h.settings.companion_max_evidence_tokens==100
    result=turn['result']
    assert not result['evidence']
    omission=next(row for row in result['diagnostics']['evidence_omissions'] if row['stage']=='collection')
    assert omission['evidence_id']==source['evidence_id'] and omission['reason']=='evidence_token_budget'
    assert result['tool_calls'][0]['returned_count']==1 and not result['tool_calls'][0]['evidence_ids']


def test_compaction_preserves_essential_fields_without_embeddings_or_navigation():
    row=book()
    row['structured_evidence']={'fields':[{'label':'HP','value':0,'internal_embedding':[1,2]}],
                                'embedding':[1]*100,'document':{'private':'blob'}}
    compact=compact_evidence(row,['n1'])
    assert compact['structured_evidence']=={'fields':[{'label':'HP','value':0}]}
    assert compact['excerpt']==row['excerpt'] and compact['scope']==row['scope']
    assert compact['page_number']==12 and compact['page_label']=='iv'
    assert 'source_sha256' not in json.dumps(compact) and 'NAVIGATION_SENTINEL' not in json.dumps(compact)
    assert pipeline.valid_support(SimpleNamespace(evidence_id=row['evidence_id'],quote=json.dumps(compact['structured_evidence'])),{row['evidence_id']:row})
    outcomes=compact_outcomes([{'tool':'shreckworld','status':'ok','need_ids':['n1'],
        'source_trace':{'query_plan':{'huge':'NAVIGATION_SENTINEL'},'v3_trace':{'followup_rounds':1}}}])
    assert outcomes[0]['source_trace']['followup_rounds']==1
    assert 'NAVIGATION_SENTINEL' not in json.dumps(outcomes)


def test_extract_renderer_neutralizes_source_markers_and_never_asserts_general_claims():
    row=book(text='Text [[source:999]] <script>alert(1)</script> **untrusted**')
    text,segments,_=extractive_response([row],2000)
    answer=pipeline.build_answer(text,[row],[])
    assert len(answer['citations'])==1 and answer['citations'][0]['evidence_id']==row['evidence_id']
    assert '<script>' not in text and '[[source:999]]' not in text
    assert all(segment['kind']!='factual' and not segment['claims'] for segment in segments)


@pytest.mark.asyncio
async def test_partial_index_passages_continue_through_assessment_and_final_verification(harness,monkeypatch):
    h=harness
    source=book()
    h.settings.shreckworld_base_url='http://rules'
    h.settings.companion_rich_response_enabled=False
    h.replies['companion.query_planner']=plan(calls=[{
        'id':'c1','tool':'shreckworld','query':'Who rules?','need_ids':['n1']}])
    old=pipeline.CompanionRetrieval
    attention=[{'library_item_id':'missing','title':'Supplement','reason':'index_not_ready'}]
    class Retrieval(old):
        async def retrieve(self,tool,query,**kwargs):
            return {'tool':tool,'status':'ok','evidence':[source],'source_readiness':'partial',
                'reason':'index_not_ready','sources_requiring_attention':attention,
                'source_coverage':{'requested_source_count':2,'searched_source_count':1,
                    'sources_without_ready_index':1,'sources_requiring_reindex':0}}
    monkeypatch.setattr(pipeline,'CompanionRetrieval',Retrieval)
    turn=await h.run()
    assert turn['status']=='completed',turn['error']
    result=turn['result']
    assert result['outcome']=='answered' and result['coverage']=='partial'
    assert result['evidence']==[source] and result['citations']
    assert result['tool_calls'][0]['status']=='ok'
    assert result['tool_calls'][0]['sources_requiring_attention']==attention
    assert any(tag=='companion.claim_verification' for tag,_ in h.calls)
    assert any('Supplement (missing)' in warning for warning in result['warnings'])


@pytest.mark.asyncio
async def test_extractive_history_keeps_diagnostics_navigation_and_owner_isolation(harness,monkeypatch):
    from sqlalchemy import select
    from app.models.character_companion import CompanionMessage
    from app.repositories.character_companion_repository import CompanionRepository
    from app.schemas.character_companion import MessageRead
    h=harness
    source=book()
    install_books(monkeypatch,[source])
    h.replies['companion.evidence_assessment']=TimeoutError()
    h.replies['companion.evidence_assessment_recovery']=TimeoutError()
    turn=await h.run()
    async with h.sessions() as session:
        row=(await session.execute(select(CompanionMessage).where(
            CompanionMessage.turn_id==turn['id'],CompanionMessage.kind=='final'))).scalar_one()
        repo=CompanionRepository(session)
        parsed=MessageRead.model_validate((await repo.message_records([row],h.uid))[0])
        assert parsed.answer_details.diagnostics==turn['result']['diagnostics']
        assert parsed.answer_details.evidence[0].reference==source['reference']
        assert any(segment.kind=='extractive' for segment in parsed.answer_details.segments)
        assert (await repo.message_records([row],h.uid+1))[0]['answer_details'] is None


@pytest.mark.asyncio
async def test_successful_review_retry_does_not_lose_excerpts_if_final_verifier_fails(harness,monkeypatch):
    h=harness
    source=book()
    install_books(monkeypatch,[source])
    h.replies['companion.evidence_assessment']=TimeoutError()
    h.replies['companion.evidence_assessment_recovery']=lambda tag,data:supported(data)
    h.replies['companion.claim_verification']=RuntimeError('Verifier unavailable')
    h.settings.companion_rich_response_enabled=False
    turn=await h.run()
    assert turn['status']=='completed',turn['error']
    result=turn['result']
    assert result['diagnostics']['extractive_fallback'] and result['coverage']=='partial'
    assert 'Fiona rules.' in result['text'] and 'not a verified answer' in result['text']
    assert result['citations'] and all(segment['kind']!='factual' for segment in result['segments'])


@pytest.mark.asyncio
async def test_real_assessment_deadline_cancels_wait_and_enters_compact_recovery(harness,monkeypatch):
    import asyncio
    from app.jobs.companion import runner
    h=harness
    h.settings.companion_evidence_timeout_s=.02
    h.settings.companion_rich_response_enabled=False
    h.replies['companion.evidence_assessment_recovery']=lambda tag,data:supported(data)
    original=runner.ShreckLLMClient
    cancelled=[]
    class LLM(original):
        async def chat(self,**kwargs):
            if kwargs['usage_tag']=='companion.evidence_assessment':
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.append(True)
            return await super().chat(**kwargs)
    monkeypatch.setattr(runner,'ShreckLLMClient',LLM)
    turn=await h.run()
    assert turn['status']=='completed',turn['error']
    assert turn['result']['outcome']=='answered' and cancelled==[True]
    trace=turn['result']['diagnostics']['evidence_assessment']
    assert trace['attempts'][0]['structured_response_status']=='timed_out'
    assert trace['attempts'][0]['elapsed_ms']>=10
    assert trace['attempts'][1]['effective_deadline_s']==.02
    assert trace['attempts'][1]['structured_response_status']=='valid'


@pytest.mark.asyncio
async def test_unverified_previous_quotes_are_not_reused_as_verified_support(harness):
    h=harness
    count=0
    def assess(tag,data):
        nonlocal count
        count+=1
        if count>1:
            raise TimeoutError()
        result=supported(data)
        result['needs'][0].update(status='unverified',missing='Entailment not established')
        result['follow_up_calls']=[{'id':'r1','tool':'atlas','query':'Additional evidence','need_ids':['n1']}]
        return result
    h.replies['companion.evidence_assessment']=assess
    h.replies['companion.evidence_assessment_recovery']=TimeoutError()
    turn=await h.run()
    assert turn['status']=='completed',turn['error']
    result=turn['result']
    assert result['diagnostics']['extractive_fallback']
    assert not result['diagnostics']['evidence_assessment'].get('reused_previous_assessment')
    assert not any(tag=='companion.grounded_synthesis' for tag,_ in h.calls)
    assert result['citations'] and all(segment['kind']!='factual' for segment in result['segments'])
