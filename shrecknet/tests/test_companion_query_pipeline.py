"""V2 scope, verification, durable commentary and migration regressions."""
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import select, create_engine, text
from app.core.config_store import Settings
from app.db.companion_query_migration import migrate_companion_query
from app.jobs.companion import runner, query_pipeline as pipeline
from app.jobs.companion.query_contracts import QueryPlan, Draft, Verification
from app.models.character_companion import CompanionChat, CompanionMessage, CompanionTurn, CompanionOutbox
from app.models.ontology import Ontology
from app.models.user import User
from app.repositories.character_companion_repository import CompanionRepository, record
from app.services.character_companion_service import CharacterCompanionService
from app.schemas.character_companion import ChatCreate, TurnCreate
from app.integrations.companion.retrieval import evidence


def plan(action='retrieve', **kw):
    return {'action':action,'ontology_update':None,'needs':[{'id':'n1','question':'Who rules?'}] if action=='retrieve' else [],
            'calls':[{'id':'c1','tool':'atlas','query':'Who rules?','need_ids':['n1']}] if action=='retrieve' else [],'clarification_questions':[],**kw}


@pytest_asyncio.fixture
async def harness(session_maker, monkeypatch):
    settings=Settings(companion_enabled=True,enable_ai_agents=True,companion_query_pipeline_version='evidence_v2')
    from app.services import character_companion_service
    monkeypatch.setattr(character_companion_service,'get_settings',lambda:settings)
    monkeypatch.setattr(runner,'get_settings',lambda:settings)
    @asynccontextmanager
    async def database():
        async with session_maker() as session: yield session
    monkeypatch.setattr(runner,'database',database)
    async def identity(turn):
        return SimpleNamespace(id=turn.user_id,role='player'),{'character_agent':{'identity_description':{'identity_summary':'Fiona'}},'personality_context':{}}
    monkeypatch.setattr(runner,'load_identity',identity)
    class Characters:
        async def get_agent(self,*a,**k): return SimpleNamespace(id='fiona',ontology_id=1,name='Fiona',status='active',identity_description={'identity_summary':'Fiona'})
        async def ensure_queryable(self,*a,**k): pass
    calls=[]; retrievals=[]; replies={}; validated=[]
    def default(tag,data):
        if tag=='companion.query_planner': return plan()
        if tag=='companion.ontology': return {'status':'resolved','ontology_id':1,'reason':'Question'}
        if tag=='companion.clarification': return 'Which world do you mean?'
        if tag=='companion.evidence_assessment': return {'needs':[{'need_id':'n1','status':'supported','supports':[{'evidence_id':data['evidence'][0]['evidence_id'],'quote':'Fiona rules.'}],'conflicting_evidence_ids':[],'missing':''}],'follow_up_calls':[]}
        if tag=='companion.grounded_synthesis': return {'segments':[{'id':'s1','kind':'factual','text':'Fiona rules.','claims':['Fiona rules.'],'supports':[{'evidence_id':data['evidence'][0]['evidence_id'],'quote':'Fiona rules.'}]}]}
        if tag=='companion.claim_verification': return {'segments':[{'segment_id':'s1','supported':True,'reason':'Matches source'}]}
        return {'changes':[]}
    class LLM:
        def __init__(self,**kw): pass
        async def aclose(self): pass
        async def chat(self,**kw):
            tag=kw['usage_tag'];data=json.loads(kw['messages'][-1]['content']);calls.append((tag,data))
            reply=replies.get(tag,default)
            if isinstance(reply,Exception): raise reply
            value=reply(tag,data) if callable(reply) else reply
            return value if isinstance(value,str) else json.dumps(value)
    monkeypatch.setattr(runner,'ShreckLLMClient',LLM)
    class Retrieval:
        def __init__(self,turn,actor): self.turn=turn
        async def retrieve(self,tool,query):
            retrievals.append((tool,query,self.turn.execution_context.copy()))
            return {'tool':tool,'status':'ok','evidence':[evidence(tool,'one','Fiona rules.',scope={'ontology_id':self.turn.execution_context.get('ontology_id')})]}
        async def validate_sources(self,rows): validated.extend(rows)
    monkeypatch.setattr(pipeline,'CompanionRetrieval',Retrieval)
    async with session_maker() as session:
        user=User(username='v2',email='v2@example.test',role='player');session.add(user)
        session.add_all([Ontology(id=1,name='First'),Ontology(id=2,name='Second')]);await session.flush()
        svc=CharacterCompanionService(session,Characters());await svc.select(user,'fiona',0)
        chat=await svc.create_chat(user,ChatCreate(character_agent_id='fiona',selection_version=1),str(uuid4()))
        uid,cid=user.id,chat['id']
    async def run(question='Who rules?',ontology=1,**kwargs):
        async with session_maker() as session:
            svc=CharacterCompanionService(session,Characters())
            admitted=await svc.admit(await session.get(User,uid),cid,TurnCreate(text=question,selection_version=1,context={'surface':'global',**({'ontology_id':ontology} if ontology else {})},**kwargs),str(uuid4()),{'expires_at':int(time.time())+600,'sid':None})
            box=(await session.execute(select(CompanionOutbox).where(CompanionOutbox.kind=='turn',CompanionOutbox.aggregate_id==admitted['turn_id']))).scalars().one();box_id=box.id
        await runner.process_outbox(box_id)
        async with session_maker() as session: return record(await session.get(CompanionTurn,admitted['turn_id']))
    return SimpleNamespace(settings=settings,replies=replies,calls=calls,retrievals=retrievals,validated=validated,run=run,sessions=session_maker,uid=uid,cid=cid)


@pytest.mark.asyncio
async def test_explicit_and_saved_scope_skip_inference_and_commentary_persists(harness):
    h=harness;r=await h.run()
    assert r['status']=='completed',r['error']
    assert r['result']['coverage']=='complete'
    assert h.validated and not any(t=='companion.ontology' for t,_ in h.calls)
    async with h.sessions() as s:
        rows=list((await s.execute(select(CompanionMessage).order_by(CompanionMessage.created_at))).scalars())
        assert [m.kind for m in rows]==['user','commentary','final']
        assert len(await CompanionRepository(s).conversation_context(h.cid,h.uid))==2
        assert (await CompanionRepository(s).message_records(rows,h.uid))[1]['answer_details'] is None
    r=await h.run(ontology=None)
    assert r['status']=='completed',r['error']
    assert not any(t=='companion.ontology' for t,_ in h.calls)


@pytest.mark.asyncio
async def test_update_runs_before_retrieval_and_persists(harness):
    h=harness;await h.run();h.retrievals.clear()
    h.replies['companion.query_planner']=lambda t,d: plan('ontology_update',ontology_update={'ontology_name':'Second','reason':'User switch'}) if d['allow_ontology_update'] else plan()
    r=await h.run('Switch to Second; who rules?',ontology=None)
    assert r['status']=='completed',r['error']
    assert h.retrievals and all(r[2]['ontology_id']==2 for r in h.retrievals)
    async with h.sessions() as s:
        chat=await s.get(CompanionChat,h.cid)
        assert chat.resolved_ontology['id']==2 and chat.scope_override


@pytest.mark.asyncio
async def test_conflicting_page_clarifies_then_keeps_override(harness):
    h=harness;h.replies['companion.query_planner']=lambda t,d: plan('ontology_update',ontology_update={'ontology_name':'Second','reason':'Switch'}) if d['ontology']['id']==1 else plan()
    r=await h.run('Switch to Second; who rules?')
    assert r['result']['outcome']=='needs_clarification' and not h.retrievals
    h.replies['companion.ontology']={'status':'resolved','ontology_id':2,'reason':'Confirmed'}
    r=await h.run('Use Second')
    assert r['status']=='completed',r['error']
    assert all(r[2]['ontology_id']==2 for r in h.retrievals)
    h.calls.clear();r=await h.run('And now?',ontology=1)
    assert r['execution_context']['ontology_id']==2
    assert not any(t=='companion.ontology' for t,_ in h.calls)


@pytest.mark.asyncio
async def test_unresolved_scope_does_not_use_character_world(harness):
    h=harness;h.replies['companion.ontology']={'status':'ambiguous','ontology_id':None,'reason':'Two worlds'}
    r=await h.run(ontology=None)
    assert r['result']['outcome']=='needs_clarification' and not h.retrievals
    async with h.sessions() as s: assert (await s.get(CompanionChat,h.cid)).active_turn_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize('tag,response',[('companion.query_planner','{}'),('companion.claim_verification',RuntimeError('offline')),('companion.claim_verification',{'segments':[{'segment_id':'s1','supported':False,'reason':'Wrong world'}]})])
async def test_invalid_planning_or_verification_never_publishes_factual_draft(harness,tag,response):
    h=harness;h.replies[tag]=response;r=await h.run()
    assert r['status']=='completed',r['error']
    assert r['result']['outcome'] in {'limited','needs_clarification'}
    assert 'Fiona rules.' not in r['result']['text']
    assert len([1 for t,_ in h.calls if t=='companion.grounded_synthesis'])<=2


@pytest.mark.asyncio
async def test_source_failure_does_not_cancel_success(harness,monkeypatch):
    h=harness;h.settings.companion_shreckworld_url='http://rules'
    h.replies['companion.query_planner']=plan(calls=[{'id':'c1','tool':'atlas','query':'canon','need_ids':['n1']},{'id':'c2','tool':'shreckworld','query':'rules','need_ids':['n1']}])
    old=pipeline.CompanionRetrieval
    class Retrieval(old):
        async def retrieve(self,tool,query):
            if tool=='shreckworld': raise RuntimeError('offline')
            return await super().retrieve(tool,query)
    monkeypatch.setattr(pipeline,'CompanionRetrieval',Retrieval)
    r=await h.run()
    assert r['status']=='completed',r['error']
    assert r['result']['evidence'] and r['result']['coverage']=='partial'
    assert {c['status'] for c in r['result']['tool_calls']}=={'ok','unavailable'}


def test_reactions_cannot_hide_claims_and_unknown_needs_fail():
    draft=Draft.model_validate({'segments':[{'id':'s1','kind':'reaction','text':'Fiona rules.','claims':['Fiona rules.'],'supports':[]}]})
    checks=Verification.model_validate({'segments':[{'segment_id':'s1','supported':True,'reason':'ok'}]})
    accepted,rejected=pipeline.accepted_segments(draft,checks,[],False)
    assert not accepted and rejected
    with pytest.raises(ValueError): QueryPlan.model_validate(plan(calls=[{'id':'c1','tool':'atlas','query':'x','need_ids':['unknown']}]))


def test_migration_preserves_content_and_is_idempotent():
    engine=create_engine('sqlite://')
    with engine.begin() as c:
        c.execute(text('CREATE TABLE character_companion_chats (id TEXT PRIMARY KEY)'))
        c.execute(text('CREATE TABLE character_companion_turns (id TEXT PRIMARY KEY)'))
        c.execute(text('CREATE TABLE character_companion_messages (id TEXT PRIMARY KEY,turn_id TEXT,chat_id TEXT,role TEXT,content TEXT,manifest JSON,committed BOOLEAN,created_at DATETIME,UNIQUE(turn_id,role))'))
        c.execute(text("INSERT INTO character_companion_messages VALUES ('m','t','c','assistant','old','[]',1,'2026-01-01')"))
        c.execute(text('CREATE TABLE character_companion_messages_v2 (id TEXT)'))
        migrate_companion_query(c);migrate_companion_query(c)
        assert c.execute(text("SELECT content,kind,sequence FROM character_companion_messages WHERE id='m'")).one()==('old','final',0)
        c.execute(text("INSERT INTO character_companion_messages VALUES ('skit','t','c','assistant','commentary',1,'hello','[]',1,'2026-01-01')"))


@pytest.mark.asyncio
async def test_recovery_is_targeted_and_bounded(harness):
    h=harness
    def assess(tag,data):
        source=data['evidence'][0]
        return {'needs':[{'need_id':'n1','status':'partial' if data['recovery_allowed'] else 'supported',
                 'supports':[{'evidence_id':source['evidence_id'],'quote':'Fiona rules.'}], 'conflicting_evidence_ids':[],'missing':'City'}],
                'follow_up_calls':[{'id':'r1','tool':'atlas','query':'Which city does Fiona rule?','need_ids':['n1']}] if data['recovery_allowed'] else []}
    h.replies['companion.evidence_assessment']=assess
    r=await h.run()
    assert r['status']=='completed',r['error']
    assert len(h.retrievals)==2 and h.retrievals[1][1]=='Which city does Fiona rule?'
    assert len([1 for tag,_ in h.calls if tag=='companion.evidence_assessment'])==2
    assert r['result']['tool_calls'][1]['recovery']


@pytest.mark.asyncio
@pytest.mark.parametrize('opt_in,policy,allowed',[(False,'labeled_opt_in',False),(True,'disabled',False),(True,'labeled_opt_in',True)])
async def test_general_knowledge_requires_both_permissions_and_is_labeled(harness,opt_in,policy,allowed):
    h=harness;h.settings.companion_general_knowledge_policy=policy
    h.replies['companion.query_planner']=plan('conversation')
    h.replies['companion.grounded_synthesis']={'segments':[{'id':'s1','kind':'general_knowledge','text':'This may vary by edition.','claims':[],'supports':[]}]}
    r=await h.run(general_knowledge_opt_in=opt_in)
    assert r['status']=='completed',r['error']
    assert ('General knowledge — not verified' in r['result']['text'])==allowed
    if not allowed: assert r['result']['outcome']=='limited'


@pytest.mark.asyncio
async def test_cancel_fences_final_but_preserves_commentary_and_selected_scope(harness,monkeypatch):
    import asyncio
    h=harness;entered=asyncio.Event()
    old=pipeline.CompanionRetrieval
    class Retrieval(old):
        async def retrieve(self,*args):
            entered.set();await asyncio.sleep(1)
            return await super().retrieve(*args)
    monkeypatch.setattr(pipeline,'CompanionRetrieval',Retrieval)
    task=asyncio.create_task(h.run())
    await asyncio.wait_for(entered.wait(),2)
    async with h.sessions() as s:
        chat=await s.get(CompanionChat,h.cid)
        turn=await s.get(CompanionTurn,chat.active_turn_id)
        await CompanionRepository(s).terminal(turn,'cancelled')
        await s.commit()
    r=await task
    assert r['status']=='cancelled' and r['result'] is None
    async with h.sessions() as s:
        messages=list((await s.execute(select(CompanionMessage).where(CompanionMessage.turn_id==r['id']))).scalars())
        assert any(m.kind=='commentary' for m in messages)
        assert not any(m.kind=='final' for m in messages)
        assert (await s.get(CompanionChat,h.cid)).resolved_ontology['id']==1


@pytest.mark.asyncio
async def test_use_page_scope_clears_override(harness):
    h=harness;await h.run()
    h.replies['companion.query_planner']=lambda t,d: plan('ontology_update',ontology_update={'ontology_name':'Second','reason':'Switch'}) if d['allow_ontology_update'] else plan()
    await h.run('Switch to Second',ontology=None)
    h.replies['companion.query_planner']=plan()
    r=await h.run('Use page',ontology=1,use_page_scope=True)
    assert r['execution_context']['ontology_id']==1
    async with h.sessions() as s: assert not (await s.get(CompanionChat,h.cid)).scope_override


def test_new_roles_and_config_validation(monkeypatch):
    from fastapi import HTTPException
    from app.api.routers import configurations
    settings=Settings()
    assert settings.model_companion_web_search.web_search_enabled
    assert settings.model_companion_web_search.provider=='openrouter'
    assert settings.model_companion_web_search.name==settings.model_companion_routing.name
    assert not settings.model_companion_evidence.web_search_enabled
    monkeypatch.setattr(configurations,'get_settings',lambda:settings)
    assert configurations._validate_updates({'companion_query_pipeline_version':'evidence_v2'})
    for payload in ({'companion_recovery_max_rounds':2},{'companion_routing_timeout_s':-1},{'companion_query_pipeline_version':'invalid'}):
        with pytest.raises(HTTPException): configurations._validate_updates(payload)


@pytest.mark.asyncio
async def test_web_adapter_uses_dedicated_target(monkeypatch):
    from app.integrations.companion import retrieval
    from app.integrations.llm import shreckllm_client
    settings=Settings(companion_query_pipeline_version='evidence_v2')
    calls=[]
    class LLM:
        def __init__(self,**kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def chat(self,**kw): calls.append(kw);return {'annotations':[]}
    monkeypatch.setattr(shreckllm_client,'ShreckLLMClient',LLM)
    monkeypatch.setattr(retrieval,'get_settings',lambda:settings)
    adapter=retrieval.CompanionRetrieval(SimpleNamespace(request={'context':{}}),None)
    assert await adapter.web('public RPG rules')==[]
    assert calls[0]['model'] is settings.model_companion_web_search
    assert calls[0]['model'].web_search_enabled


@pytest.mark.asyncio
async def test_explicit_foundry_scope_skips_inference_and_preserves_original_request(harness):
    from app.models.world import World
    h=harness
    async with h.sessions() as session:
        session.add(World(id='table',name='Table'))
        await session.flush()
        ontology=await session.get(Ontology,1);ontology.world_id='table'
        await session.commit()
    snapshot={'version':1,'captured_at':'2026-11-08T12:00:00Z','documents':[],
              'table':{'foundry_world_id':'game','title':'Table','foundry_version':'13','system_id':'test','system_title':'Rules','system_version':'1','shrecknet_world_id':'table'}}
    result=await h.run(foundry_context=snapshot)
    assert result['status']=='completed',result['error']
    assert not any(tag=='companion.ontology' for tag,_ in h.calls)
    assert 'character_ontology_id' not in result['request']['context']
    assert result['execution_context']['character_ontology_id']==1


def test_structured_snapshot_support_is_validated_without_inventing_prose():
    from app.jobs.companion.query_contracts import Support
    source=evidence('foundry','Actor.a','',structured={'fields':[{'label':'HP','value':10}]})
    support=Support(evidence_id=source['evidence_id'],quote='"label": "HP", "value": 10')
    assert pipeline.valid_support(support,{source['evidence_id']:source})
    assert not pipeline.valid_support(Support(evidence_id=source['evidence_id'],quote='"value": 99'),{source['evidence_id']:source})
