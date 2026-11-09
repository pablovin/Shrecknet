"""Markdown generation, evidence aggregation, source access and durable diagnostics."""
import asyncio
import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.jobs.companion import query_pipeline as pipeline, query_prompts
from app.jobs.companion.evidence_context import compact_evidence, compact_outcomes
from app.jobs.companion.contracts import build_answer
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



@pytest.mark.asyncio
async def test_projection_budget_keeps_full_evidence_and_records_omission_reason(harness,monkeypatch):
    h=harness
    h.settings.companion_max_evidence_tokens=100
    source=book(text='Long passage. '*300)
    install_books(monkeypatch,[source])
    turn=await h.run()
    assert turn['status']=='completed',turn['error']
    assert h.settings.companion_max_evidence_tokens==100
    result=turn['result']
    assert result['evidence']==[source]
    omission=next(row for row in result['diagnostics']['evidence_omissions'] if row['stage']=='synthesis')
    assert omission['evidence_id']==source['evidence_id'] and omission['reason']=='evidence_token_budget'
    assert result['tool_calls'][0]['returned_count']==1 and result['tool_calls'][0]['evidence_ids']==[source['evidence_id']]



def test_compaction_preserves_essential_fields_without_embeddings_or_navigation():
    row=book()
    row['structured_evidence']={'fields':[{'label':'HP','value':0,'internal_embedding':[1,2]}],
                                'embedding':[1]*100,'document':{'private':'blob'}}
    compact=compact_evidence(row,['n1'])
    assert compact['structured_evidence']=={'fields':[{'label':'HP','value':0}]}
    assert compact['excerpt']==row['excerpt'] and compact['scope']==row['scope']
    assert compact['page_number']==12 and compact['page_label']=='iv'
    assert 'source_sha256' not in json.dumps(compact) and 'NAVIGATION_SENTINEL' not in json.dumps(compact)
    outcomes=compact_outcomes([{'tool':'shreckworld','status':'ok','need_ids':['n1'],
        'source_trace':{'query_plan':{'huge':'NAVIGATION_SENTINEL'},'v3_trace':{'followup_rounds':1}}}])
    assert outcomes[0]['source_trace']['followup_rounds']==1
    assert 'NAVIGATION_SENTINEL' not in json.dumps(outcomes)



@pytest.mark.asyncio
async def test_partial_index_passages_proceed_directly_to_synthesis(harness,monkeypatch):
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
    assert not any(tag=='companion.claim_verification' for tag,_ in h.calls)
    assert any('Supplement (missing)' in warning for warning in result['warnings'])



@pytest.mark.asyncio
async def test_markdown_history_keeps_diagnostics_navigation_and_owner_isolation(harness,monkeypatch):
    from sqlalchemy import select
    from app.models.character_companion import CompanionMessage
    from app.repositories.character_companion_repository import CompanionRepository
    from app.schemas.character_companion import MessageRead
    h=harness
    source=book()
    install_books(monkeypatch,[source])
    turn=await h.run()
    async with h.sessions() as session:
        row=(await session.execute(select(CompanionMessage).where(
            CompanionMessage.turn_id==turn['id'],CompanionMessage.kind=='final'))).scalar_one()
        repo=CompanionRepository(session)
        parsed=MessageRead.model_validate((await repo.message_records([row],h.uid))[0])
        assert parsed.answer_details.diagnostics==turn['result']['diagnostics']
        assert parsed.answer_details.evidence[0].reference==source['reference']
        assert all(segment.kind=='conversation' for segment in parsed.answer_details.segments)
        assert (await repo.message_records([row],h.uid+1))[0]['answer_details'] is None



@pytest.mark.asyncio
async def test_truncation_retries_once_with_identical_evidence_and_aliases(harness):
    h = harness
    attempts = []
    def synthesize(tag, data):
        attempts.append(data)
        return {'text': 'Incomplete' if len(attempts) == 1 else 'Fiona rules.[[source:1]]',
                'response_metadata': {'finish_reason': 'length' if len(attempts) == 1 else 'stop'}}
    h.replies['companion.grounded_synthesis'] = synthesize
    turn = await h.run()
    assert turn['status'] == 'completed', turn['error']
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert turn['result']['text'] == 'Fiona rules.'
    assert turn['result']['diagnostics']['synthesis_truncations'] == 1
    assert turn['metrics']['citation_validation']['valid'] == 1
    assert not turn['draft']  # No private failed attempt is exposed as a stream.


@pytest.mark.asyncio
@pytest.mark.parametrize('response,code', [
    ({'text': 'Incomplete', 'response_metadata': {'finish_reason': 'length'}}, 'synthesis_truncated'),
    ({'text': '', 'response_metadata': {'finish_reason': 'stop'}}, 'empty_synthesis'),
    ({'text': '[[source:1]]', 'response_metadata': {'finish_reason': 'stop'}}, 'empty_synthesis'),
    (RuntimeError('provider secret'), 'synthesis_generation_failed'),
])
async def test_generation_failure_is_not_missing_evidence_and_never_enqueues_rapport(harness, response, code):
    from sqlalchemy import select
    from app.models.character_companion import CompanionMessage, CompanionOutbox, CompanionEvent
    h = harness
    h.replies['companion.grounded_synthesis'] = response
    turn = await h.run()
    assert turn['status'] == 'failed' and turn['error']['code'] == code
    assert turn['result'] is None
    assert turn['metrics']['stages'][-1]['stage'] == 'companion.grounded_synthesis'
    assert 'provider secret' not in json.dumps(turn)
    async with h.sessions() as session:
        assert not list((await session.execute(select(CompanionMessage).where(
            CompanionMessage.turn_id == turn['id'], CompanionMessage.kind == 'final'))).scalars())
        assert not list((await session.execute(select(CompanionOutbox).where(
            CompanionOutbox.aggregate_id == turn['id'], CompanionOutbox.kind == 'rapport'))).scalars())
        assert not list((await session.execute(select(CompanionEvent).where(
            CompanionEvent.turn_id == turn['id'], CompanionEvent.type == 'response.final'))).scalars())
    assert len([tag for tag, _ in h.calls if tag == 'companion.grounded_synthesis']) == (2 if code == 'synthesis_truncated' else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [ValueError('source_changed'), HTTPException(409), HTTPException(403), TimeoutError()])
async def test_source_revocation_or_unverifiable_access_fails_closed(harness, monkeypatch, failure):
    from sqlalchemy import select
    from app.models.character_companion import CompanionEvent, CompanionOutbox
    h = harness
    async def validate(self, rows):
        raise failure
    monkeypatch.setattr(pipeline.CompanionRetrieval, 'validate_sources', validate)
    turn = await h.run()
    assert turn['status'] == 'failed' and turn['result'] is None
    assert turn['error']['code'] == ('source_changed' if isinstance(failure, ValueError) or getattr(failure,'status_code',None)==409
        else 'source_access_denied' if isinstance(failure,HTTPException) else 'source_validation_failed')
    async with h.sessions() as session:
        assert not list((await session.execute(select(CompanionEvent).where(
            CompanionEvent.turn_id == turn['id'], CompanionEvent.type.in_(['response.delta','response.final'])))).scalars())
        assert not list((await session.execute(select(CompanionOutbox).where(
            CompanionOutbox.aggregate_id == turn['id'], CompanionOutbox.kind == 'rapport'))).scalars())


@pytest.mark.asyncio
async def test_mixed_tools_start_concurrently_and_synthesize_successful_evidence(harness, monkeypatch):
    h = harness
    h.settings.shreckworld_base_url = 'http://rules'
    h.replies['companion.query_planner'] = {'mode': 'search', 'tasks': [
        {'query': 'Telmori character creation rules', 'sources': ['shreckworld','web']}],
        'clarification_questions': []}
    entered = set()
    barrier = asyncio.Event()
    async def retrieve(self, tool, query, **kwargs):
        entered.add(tool)
        if len(entered) == 2:
            barrier.set()
        await asyncio.wait_for(barrier.wait(), 1)
        h.retrievals.append((tool,query,self.turn.execution_context))
        return {'tool':tool,'status':'ok','evidence':[evidence(tool,tool,
            'Creation requires choosing the applicable edition and consulting its creation steps.')], 'elapsed_ms':1}
    monkeypatch.setattr(pipeline.CompanionRetrieval, 'retrieve', retrieve)
    h.replies['companion.grounded_synthesis'] = 'Use the creation steps for your edition.[[source:1]][[source:2]]'
    turn = await h.run('How do I create a Telmori character?')
    assert turn['status'] == 'completed', turn['error']
    assert entered == {'shreckworld','web'} and len(turn['result']['citations']) == 2
    assert 'could not verify' not in turn['result']['text']
    assert len(turn['metrics']['tools']) == 2
    removed = {'companion.memory_relevance','companion.evidence_assessment',
               'companion.evidence_assessment_recovery','companion.claim_verification','companion.interim'}
    assert not removed.intersection(tag for tag,_ in h.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['empty','unavailable','timed_out'])
async def test_one_source_without_results_does_not_invalidate_another(harness, monkeypatch, status):
    h = harness
    h.replies['companion.query_planner'] = {'mode':'search','tasks':[
        {'query':'Who rules?', 'sources':['atlas','web']}], 'clarification_questions':[]}
    original = pipeline.CompanionRetrieval.retrieve
    async def retrieve(self, tool, query, **kwargs):
        if tool == 'web':
            return {'tool':tool,'status':status,'evidence':[]}
        return await original(self,tool,query,**kwargs)
    monkeypatch.setattr(pipeline.CompanionRetrieval,'retrieve',retrieve)
    turn = await h.run()
    assert turn['status']=='completed' and turn['result']['outcome']=='answered'
    assert turn['result']['text']=='Fiona rules.' and len(turn['result']['citations'])==1
    assert turn['result']['coverage']==('unknown' if status=='empty' else 'partial')


@pytest.mark.asyncio
async def test_casual_chat_preserves_identity_rapport_without_retrieval_or_generated_interims(harness):
    from sqlalchemy import select
    from app.models.character_companion import CompanionMessage, CompanionOutbox
    h=harness
    h.settings.companion_interim_enabled=True
    h.settings.companion_interim_max_messages=3
    h.replies['companion.query_planner']=plan('conversation')
    h.replies['companion.grounded_synthesis']='Well then, come sit beside me.'
    turn=await h.run('Hello Fiona')
    assert turn['status']=='completed' and not h.retrievals
    data=next(data for tag,data in h.calls if tag=='companion.grounded_synthesis')
    assert data['identity_description']['identity_summary']=='Fiona'
    assert 'personality_context' in data and data['rapport']['scores']['trust']==20
    assert all(tag!='companion.interim' for tag,_ in h.calls)
    async with h.sessions() as session:
        comments=list((await session.execute(select(CompanionMessage).where(
            CompanionMessage.turn_id==turn['id'],CompanionMessage.kind=='commentary'))).scalars())
        assert len(comments)==1
        rapport=list((await session.execute(select(CompanionOutbox).where(
            CompanionOutbox.aggregate_id==turn['id'],CompanionOutbox.kind=='rapport'))).scalars())
        assert len(rapport)==1 and rapport[0].status=='pending'


@pytest.mark.asyncio
async def test_router_clarification_uses_existing_pending_state(harness):
    from app.models.character_companion import CompanionChat
    h=harness
    h.replies['companion.query_planner']={'mode':'clarify','tasks':[],
        'clarification_questions':['Which edition do you mean?']}
    turn=await h.run()
    assert turn['result']['outcome']=='needs_clarification' and not h.retrievals
    async with h.sessions() as session:
        chat=await session.get(CompanionChat,h.cid)
        assert chat.pending_clarification['questions']==['Which edition do you mean?']


def test_aliases_use_only_the_supplied_projection_and_reject_unknown_and_malformed_markers():
    first=book('first'); second=book('second')
    result=build_answer('Supported.[[source:1]] Unknown.[[source:2]] Broken.[[source:9',
        [first,second],[],citation_evidence=[second])
    assert result['citations'][0]['evidence_id']==second['evidence_id']
    assert len(result['citations'])==1 and '[[source:' not in result['text']
    assert result['citation_validation']=={'valid':1,'unknown':1,'malformed':True}
    assert result['coverage']=='partial' and len(result['warnings'])==2
    assert result['evidence']==[first,second]
    multiline=build_answer('Text [[source:bad\ncontinues.',[],[])
    assert '[[source:' not in multiline['text'] and multiline['citation_validation']['malformed']


@pytest.mark.asyncio
async def test_invalid_citations_have_diagnostics_but_never_invent_links(harness):
    h=harness
    h.replies['companion.grounded_synthesis']='The source is uncertain.[[source:999]]'
    turn=await h.run()
    assert turn['status']=='completed'
    assert turn['result']['citations']==[] and turn['result']['rich_response'] is None
    assert turn['metrics']['citation_validation']['unknown']==1
    assert turn['result']['coverage']=='partial'


@pytest.mark.asyncio
async def test_internal_rules_question_selects_books_without_web_or_memory_vote(harness):
    h=harness
    h.settings.shreckworld_base_url='http://rules'
    h.replies['companion.query_planner']={'mode':'search','tasks':[
        {'query':'Indexed character creation rules','sources':['shreckworld']}], 'clarification_questions':[]}
    turn=await h.run('Explain the indexed creation rules')
    assert turn['status']=='completed'
    assert [tool for tool,_,_ in h.retrievals]==['shreckworld']
    assert all(tag not in {'companion.memory_relevance','companion.evidence_assessment'} for tag,_ in h.calls)


@pytest.mark.asyncio
async def test_character_memory_remains_subjective_evidence_in_the_synthesis_input(harness):
    h=harness
    h.replies['companion.query_planner']={'mode':'search','tasks':[
        {'query':'Your recollection of the ruler','sources':['character_memory']}], 'clarification_questions':[]}
    h.replies['companion.grounded_synthesis']='As I remember it, Fiona ruled.[[source:1]]'
    turn=await h.run('What do you remember about the ruler?')
    data=next(data for tag,data in h.calls if tag=='companion.grounded_synthesis')
    assert data['evidence'][0]['epistemic_category']=='subjective_memory'
    assert turn['result']['citations'][0]['source_type']=='character_memory'
    assert turn['result']['text'].startswith('As I remember it')
    assert [tool for tool,_,_ in h.retrievals]==['character_memory']


def test_inactive_model_roles_are_not_required_to_enable_companion(monkeypatch):
    from app.core.config_store import Settings
    from app.api.routers import configurations
    settings=Settings()
    monkeypatch.setattr(configurations,'get_settings',lambda:settings)
    result=configurations._validate_updates({'companion_enabled':True,
        'companion_interim_enabled':True,
        'model_companion_evidence':{'provider':'','name':''},
        'model_companion_interim':{'provider':'','name':''}})
    assert result['companion_enabled']


@pytest.mark.asyncio
@pytest.mark.parametrize('interpretation_fails',[False,True])
async def test_image_search_enrichment_can_reach_web_and_optional_failure_preserves_answer(harness,monkeypatch,tmp_path,interpretation_fails):
    import hashlib
    from uuid import uuid4
    from PIL import Image
    from app.jobs.companion import runner
    from app.models.character_companion import CompanionAttachment
    h=harness
    identifier=str(uuid4()); path=tmp_path/identifier
    Image.new('RGB',(8,8)).save(path,format='PNG')
    monkeypatch.setattr(runner,'private_root',lambda:tmp_path)
    async with h.sessions() as session:
        session.add(CompanionAttachment(id=identifier,user_id=h.uid,storage_key=identifier,
            size=path.stat().st_size,width=8,height=8,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        await session.commit()
    h.replies['companion.query_planner']={'mode':'search','tasks':[
        {'query':'Explain this pictured rule','sources':['web']}], 'clarification_questions':[]}
    h.replies['companion.image_interpretation']=(RuntimeError('optional interpretation') if interpretation_fails else
        {'observations':['A character sheet'],'uncertainties':[],'retrieval_terms':['Telmori']})
    turn=await h.run('Explain this image',attachment_ids=[identifier])
    assert turn['status']=='completed',turn['error']
    assert bool('Telmori' in h.retrievals[0][1])==(not interpretation_fails)
    data=next(data for tag,data in h.calls if tag=='companion.grounded_synthesis')
    assert bool(data['image_observations'])==(not interpretation_fails)
    assert bool(turn['result']['diagnostics'].get('image_interpretation_error'))==interpretation_fails
