"""Companion isolation, idempotency, terminal races and rapport regressions."""
import json
import time
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.models.user import User
from app.models.character_companion import CompanionTurn, CompanionMessage
from app.services.character_companion_service import CharacterCompanionService
from app.schemas.character_companion import ChatCreate, TurnCreate, VersionWrite
from app.jobs.companion.contracts import RapportProposal, Answer, provisional_text
from app.repositories.character_companion_repository import cursor, decode_cursor


class Characters:
    async def get_agent(self, identifier, public_only=True):
        if identifier == 'private' and public_only:
            raise HTTPException(404)
        return SimpleNamespace(id=identifier, ontology_id=12, name='Fiona', subtitle=None, image_url=None, status='active', identity_description={'identity_summary':'Fiona','psychological_summary':'Duty','personality_traits':[]})

    async def ensure_queryable(self, identifier, public_only=True):
        await self.get_agent(identifier, public_only)
        if identifier == 'archived':
            raise HTTPException(409)


async def owner(session, suffix='a'):
    user = User(username=suffix, email=f'{suffix}@example.test', role='player')
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_selection_switch_clear_and_pair_isolation(session_maker):
    async with session_maker() as session:
        user = await owner(session)
        svc = CharacterCompanionService(session, Characters())
        assert (await svc.selection(user))['version'] == 0
        await svc.select(user, 'fiona', 0)
        await svc.select(user, 'shrek', 1)
        await svc.select(user, 'fiona', 2)
        assert (await svc.rapport(user, 'fiona'))['scores']['trust'] == 20
        cleared = await svc.select(user, None, 3)
        assert cleared['version'] == 4 and cleared['availability'] == 'none'
        with pytest.raises(HTTPException) as error:
            await svc.select(user, 'fiona', 3)
        assert error.value.status_code == 409
        with pytest.raises(HTTPException):
            await svc.select(user, 'private', 4)
        assert (await svc.selection(user))['version'] == 4


@pytest.mark.asyncio
async def test_chat_idempotency_payload_conflict_and_user_isolation(session_maker):
    async with session_maker() as session:
        user = await owner(session)
        other = await owner(session, 'b')
        svc = CharacterCompanionService(session, Characters())
        await svc.select(user, 'fiona', 0)
        payload = ChatCreate(character_agent_id='fiona', selection_version=1)
        key = str(uuid4())
        first = await svc.create_chat(user, payload, key)
        assert await svc.create_chat(user, payload, key) == first
        with pytest.raises(HTTPException) as error:
            await svc.create_chat(user, payload.model_copy(update={'title':'different'}), key)
        assert error.value.detail['code'] == 'idempotency_conflict'
        with pytest.raises(HTTPException) as error:
            await svc.chat(other, first['id'])
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_admission_cancel_completion_race_and_reset_generation(session_maker, monkeypatch):
    from app.services import character_companion_service as module
    settings = SimpleNamespace(companion_enabled=True, enable_ai_agents=True, model_companion_synthesis=SimpleNamespace(name='model'), model_companion_rapport_update=SimpleNamespace(name='model'), companion_web_enabled=False)
    monkeypatch.setattr(module, 'get_settings', lambda: settings)
    async with session_maker() as session:
        user = await owner(session)
        svc = CharacterCompanionService(session, Characters())
        await svc.select(user, 'fiona', 0)
        chat = await svc.create_chat(user, ChatCreate(character_agent_id='fiona', selection_version=1), str(uuid4()))
        payload = TurnCreate(text='Hello', selection_version=1)
        key = str(uuid4())
        principal = {'expires_at':int(time.time())+60,'sid':None}
        admitted = await svc.admit(user, chat['id'], payload, key, principal)
        assert await svc.admit(user, chat['id'], payload, key, principal) == admitted
        with pytest.raises(HTTPException) as busy:
            await svc.admit(user, chat['id'], payload, str(uuid4()), principal)
        assert busy.value.detail['code'] == 'chat_busy'
        await svc.cancel(user, admitted['turn_id'])
        turn = await session.get(CompanionTurn, admitted['turn_id'])
        assert not await svc.repo.terminal(turn, 'completed', result={'text':'late','citations':[]})
        assert (await svc.rapport(user, 'fiona'))['pending_count'] == 0
        await svc.reset(user, 'fiona', VersionWrite(expected_version=0), str(uuid4()))
        assert (await svc.rapport(user, 'fiona'))['generation'] == 1
        assert list((await session.execute(select(CompanionMessage).where(CompanionMessage.committed.is_(True)))).scalars()) == []


@pytest.mark.parametrize('delta,confidence,quote,expected',[(99,.9,'promise',22),(-99,.9,'promise',18),(2,.2,'promise',20),(2,.9,'fabricated',20),(0,.9,'promise',20)])
def test_rapport_signed_bounded_zero_and_exchange_evidence(delta, confidence, quote, expected):
    proposal = RapportProposal.model_validate({'changes':[{'dimension':'trust','delta':delta,'confidence':confidence,'evidence':[{'message_id':'u','quote':quote}],'reason':'character perspective'}]})
    assert proposal.apply({'trust':20},[{'id':'u','content':'I kept my promise'}])['trust'] == expected


def test_rapport_rejects_duplicate_dimension_nonfinite_and_coercion():
    change = {'dimension':'trust','delta':True,'confidence':.8,'evidence':[],'reason':''}
    with pytest.raises(ValueError):
        RapportProposal.model_validate({'changes':[change]})
    change['delta'] = 1
    with pytest.raises(ValueError):
        RapportProposal.model_validate({'changes':[change,change]})
    change['confidence'] = float('nan')
    with pytest.raises(ValueError):
        RapportProposal.model_validate({'changes':[change]})


def test_reference_validation_and_partial_json_projection():
    raw = json.dumps({'segments':[{'id':'s1','kind':'fact','text':'A fact','evidence_ids':['missing']}],'coverage':'complete','warnings':[]})
    assert provisional_text(raw) == 'A fact'
    with pytest.raises(ValueError, match='Unknown evidence'):
        Answer.model_validate_json(raw).final([],[])
    assert provisional_text('{"segments":[{"text":"Hel') == 'Hel'
    value = cursor(4,123)
    assert decode_cursor(value,4) == 123
    with pytest.raises(HTTPException):
        decode_cursor(value,5)


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid_answer',[False,True])
async def test_durable_worker_completion_and_duplicate_rapport_delivery(session_maker, monkeypatch,invalid_answer):
    from contextlib import asynccontextmanager
    from app.jobs.companion import runner
    from app.core.config_store import Settings, LLMModelTarget
    from app.services import character_companion_service
    from app.models.character_companion import CompanionOutbox, CompanionRapportAudit, CompanionEvent
    target=LLMModelTarget(provider='test',name='test')
    settings=Settings(companion_enabled=True,enable_ai_agents=True,model_companion_synthesis=target,model_companion_rapport_update=target)
    monkeypatch.setattr(character_companion_service,'get_settings',lambda:settings)
    monkeypatch.setattr(runner,'get_settings',lambda:settings)
    @asynccontextmanager
    async def database():
        async with session_maker() as session:
            yield session
    monkeypatch.setattr(runner,'database',database)
    async def identity(turn):
        return SimpleNamespace(id=turn.user_id,role='player'), {'character_agent':{'identity_description':{'identity_summary':'Fiona','psychological_summary':'Duty','personality_traits':[]}},'personality_context':{}}
    monkeypatch.setattr(runner,'load_identity',identity)
    class LLM:
        calls=0
        def __init__(self,**kwargs):pass
        async def aclose(self):pass
        async def stream_chat(self,**kwargs):
            LLM.calls+=1
            raw=json.dumps({'segments':[{'id':'s1','kind':'fact' if invalid_answer else 'conversation','text':'Hello, friend.','evidence_ids':[]}],'coverage':'complete','warnings':[]})
            for part in (raw[:45],raw[45:60],raw[60:]):yield part
        async def chat(self,**kwargs):return '{"changes":[]}'
    monkeypatch.setattr(runner,'ShreckLLMClient',LLM)
    async with session_maker() as session:
        user=await owner(session)
        svc=CharacterCompanionService(session,Characters())
        await svc.select(user,'fiona',0)
        chat=await svc.create_chat(user,ChatCreate(character_agent_id='fiona',selection_version=1),str(uuid4()))
        admitted=await svc.admit(user,chat['id'],TurnCreate(text='hello',selection_version=1),str(uuid4()),{'expires_at':int(time.time())+600,'sid':None})
        outbox=(await session.execute(select(CompanionOutbox).where(CompanionOutbox.kind=='turn'))).scalars().one()
        outbox_id=outbox.id
    await runner.process_outbox(outbox_id)
    await runner.process_outbox(outbox_id)
    async with session_maker() as session:
        turn=await session.get(CompanionTurn,admitted['turn_id'])
        if invalid_answer:
            assert turn.status=='failed' and turn.result is None
            assert turn.metrics['failure_stage']=='validating'
            assert turn.metrics['validation_rule']=='Missing evidence'
            assert turn.rapport_status=='not_applicable'
            assert not list((await session.execute(select(CompanionMessage).where(CompanionMessage.turn_id==turn.id,CompanionMessage.role=='assistant'))).scalars())
            assert LLM.calls==1
            return
        assert turn.status=='completed' and turn.result['text']=='Hello, friend.'
        assert turn.draft=='Hello, friend.'
        names=[row.type for row in (await session.execute(select(CompanionEvent).order_by(CompanionEvent.id))).scalars()]
        assert names.index('response.final') < names.index('turn.completed')
        rapport_outbox=(await session.execute(select(CompanionOutbox).where(CompanionOutbox.kind=='rapport'))).scalars().one().id
    await runner.process_outbox(rapport_outbox)
    await runner.process_outbox(rapport_outbox)
    async with session_maker() as session:
        assert len(list((await session.execute(select(CompanionRapportAudit).where(CompanionRapportAudit.turn_id==admitted['turn_id']))).scalars()))==1
    assert LLM.calls==1


@pytest.mark.asyncio
async def test_http_schema_ownership_cancel_and_sse_replay(client,session_maker):
    from app.main import app
    from app.api.routers.character_agents import service as character_service
    from app.core.security import create_access_token
    from app.models.character_companion import CompanionOutbox
    from app.api.routers.character_companion import service as companion_service
    async with session_maker() as session:
        user=await owner(session)
        await session.commit()
        user_id=user.id
    app.dependency_overrides[character_service]=lambda:Characters()
    headers={'Authorization':'Bearer '+create_access_token(str(user_id),'player')}
    root='/users/me/character-companion'
    assert (await client.get(root+'/selection')).status_code==401
    selected=await client.put(root+'/selection',headers=headers,json={'character_agent_id':'fiona','expected_version':0})
    assert selected.status_code==200 and selected.json()['version']==1
    created=await client.post(root+'/chats',headers={**headers,'Idempotency-Key':str(uuid4())},json={'character_agent_id':'fiona','selection_version':1})
    assert created.status_code==201,created.text
    chat_id=created.json()['id']
    async with session_maker() as session:
        turn=CompanionTurn(id=str(uuid4()),chat_id=chat_id,user_id=user_id,character_agent_id='fiona',request={'text':'hello'},principal={'expires_at':int(time.time())+60,'sid':None},status='queued')
        session.add(turn);await session.flush()
        from app.repositories.character_companion_repository import CompanionRepository
        repo=CompanionRepository(session)
        await repo.event(user_id,'turn.created',{'turn_id':turn.id},chat_id=chat_id,turn_id=turn.id)
        await repo.terminal(turn,'cancelled')
        await session.commit()
        turn_id=turn.id
    snapshot=await client.get(root+'/turns/'+turn_id,headers=headers)
    assert snapshot.status_code==200 and snapshot.json()['status']=='cancelled'
    assert 'principal' not in snapshot.json() and 'lease' not in snapshot.json()
    stream=await client.get(root+'/turns/'+turn_id+'/events',headers=headers)
    assert stream.status_code==200 and 'event: turn.cancelled' in stream.text
    assert stream.headers['content-type'].startswith('text/event-stream')
    assert stream.headers['cache-control']=='no-store'
    schema=app.openapi()
    assert root+'/chats/{chat_id}/turns' in schema['paths']

@pytest.mark.asyncio
async def test_optional_router_failure_and_required_web_greeting(monkeypatch):
    from app.jobs.companion import runner
    settings = SimpleNamespace(companion_web_enabled=True, companion_web_search_url='https://search.example', companion_model_routing_enabled=True, model_companion_routing=SimpleNamespace(name='router'), companion_tool_timeout_s=.1)
    monkeypatch.setattr(runner, 'get_settings', lambda: settings)
    class BrokenRouter:
        async def chat(self, **kwargs):
            return '{invalid'
    turn = SimpleNamespace(request={'text':'Can we talk?', 'tools':{'allowed':None,'web':'disabled'}, 'context':None})
    assert await runner.route(turn, BrokenRouter(), []) == []
    turn.request['text'] = 'hello'
    turn.request['tools']['web'] = 'require'
    assert await runner.route(turn, BrokenRouter(), []) == ['web']


@pytest.mark.asyncio
async def test_deleted_exchange_settles_rapport_without_model(session_maker, monkeypatch):
    from contextlib import asynccontextmanager
    from app.jobs.companion import runner
    from app.models.character_companion import CompanionRapport, CompanionOutbox, CompanionRapportAudit
    from sqlalchemy import select
    @asynccontextmanager
    async def database():
        async with session_maker() as session:
            yield session
    monkeypatch.setattr(runner, 'database', database)
    monkeypatch.setattr(runner, 'get_settings', lambda: SimpleNamespace())
    async with session_maker() as session:
        user = await owner(session)
        svc = CharacterCompanionService(session, Characters())
        await svc.select(user, 'fiona', 0)
        chat = await svc.create_chat(user, ChatCreate(character_agent_id='fiona', selection_version=1), str(uuid4()))
        turn = CompanionTurn(user_id=user.id, character_agent_id='fiona', chat_id=chat['id'], request={}, principal={}, status='completed', completion_sequence=1, generation=0)
        session.add(turn)
        await session.flush()
        pair = await svc.repo.pair(user.id, 'fiona')
        pair.next_sequence = 1
        session.add(CompanionOutbox(kind='rapport', aggregate_id=turn.id))
        identifier = turn.id
        await session.commit()
    async with session_maker() as session:
        row = await session.get(CompanionTurn, identifier)
        turn_input = SimpleNamespace(**{column.name:getattr(row,column.name) for column in row.__table__.columns})
    assert await runner.evaluate_rapport(turn_input)
    async with session_maker() as session:
        audit = (await session.execute(select(CompanionRapportAudit))).scalars().one()
        assert audit.disposition == 'deleted_exchange'
        pair = (await session.execute(select(CompanionRapport))).scalars().one()
        assert pair.evaluated_sequence == 1 and pair.trust == 20

@pytest.mark.asyncio
async def test_evidence_cache_is_scoped_and_rechecks_current_access(monkeypatch):
    from datetime import timedelta
    from app.integrations.companion.retrieval import CompanionRetrieval
    from app.models.character_companion import utcnow
    retrieval = CompanionRetrieval.__new__(CompanionRetrieval)
    retrieval.scope = {'ontology_id':12}
    retrieval.settings = SimpleNamespace(companion_tool_timeout_s=.1)
    calls = []
    async def validate(rows):
        calls.append(rows)
    retrieval.validate_sources = validate
    previous = {'query':'Who is Fiona?', 'scope':{'ontology_id':12}, 'created_at':utcnow(), 'evidence':[{'source_type':'atlas','evidence_id':'a'}]}
    assert (await retrieval.cached('atlas','Tell me more',previous))['cache_hit']
    assert len(calls) == 1
    assert await retrieval.cached('atlas','An unrelated question',previous) is None
    assert await retrieval.cached('atlas','Tell me more',{**previous,'scope':{'ontology_id':13}}) is None
    assert await retrieval.cached('atlas','Tell me more',{**previous,'created_at':utcnow()-timedelta(minutes=6)}) is None
    async def revoked(rows):
        raise ValueError('permission_revoked')
    retrieval.validate_sources = revoked
    assert await retrieval.cached('atlas','Tell me more',previous) is None

@pytest.mark.asyncio
async def test_private_image_normalization_ownership_and_deletion(session_maker, tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image
    from starlette.datastructures import UploadFile
    from app.services import companion_attachment_service as attachments
    monkeypatch.setattr(attachments,'private_root',lambda:tmp_path)
    raw=BytesIO()
    Image.new('RGB',(8,8),'red').save(raw,format='JPEG')
    async with session_maker() as session:
        first,second=await owner(session),await owner(session,'other')
        service=attachments.CompanionAttachmentService(session)
        uploaded=await service.upload(first.id,UploadFile(filename='photo.jpg',file=BytesIO(raw.getvalue())))
        assert uploaded['mime_type']=='image/png' and 'storage_key' not in uploaded
        with pytest.raises(HTTPException) as error:
            await service.get(second.id,uploaded['id'])
        assert error.value.status_code==404
        assert len(list(tmp_path.iterdir()))==1
        from app.core.config_store import Settings
        from app.services import character_companion_service as companion_module
        monkeypatch.setattr(companion_module,'get_settings',lambda:Settings(companion_enabled=True,enable_ai_agents=True))
        companion=CharacterCompanionService(session,Characters())
        await companion.select(first,'fiona',0)
        chat=await companion.create_chat(first,ChatCreate(character_agent_id='fiona',selection_version=1),str(uuid4()))
        admitted=await companion.admit(first,chat['id'],TurnCreate(text='Look at this image',attachment_ids=[uploaded['id']],selection_version=1),str(uuid4()),{'expires_at':int(time.time())+60,'sid':None})
        user_message=(await session.execute(select(CompanionMessage).where(CompanionMessage.turn_id==admitted['turn_id']))).scalars().one()
        assert user_message.manifest[0]['source_type']=='user_attachment'
        assert user_message.manifest[0]['reference']['attachment_id']==uploaded['id']
        assert 'storage_key' not in json.dumps(user_message.manifest)
        await service.delete(first.id,uploaded['id'])
        assert not list(tmp_path.iterdir())
        await service.delete(first.id,uploaded['id'])
        with pytest.raises(HTTPException):
            await service.get(first.id,uploaded['id'])
        with pytest.raises(HTTPException) as error:
            await service.upload(first.id,UploadFile(filename='bad.png',file=BytesIO(b'not an image')))
        assert error.value.status_code==415

@pytest.mark.asyncio
@pytest.mark.parametrize('completed',[True,False])
async def test_gateway_stream_rejects_truncation_and_does_not_reuse_usage(monkeypatch,completed):
    import httpx
    from app.integrations.llm.shreckllm_client import ShreckLLMClient
    from app.core.config_store import LLMModelTarget
    client=ShreckLLMClient(base_url='http://gateway')
    await client._http.aclose()
    async def response(request):
        text='data: {"type":"delta","text":"hello"}\n\n'
        if completed:
            text+='data: {"type":"done"}\n\n'
        return httpx.Response(200,text=text,headers={'content-type':'text/event-stream'})
    client._http=httpx.AsyncClient(base_url='http://gateway',transport=httpx.MockTransport(response))
    async def ready(provider):pass
    monkeypatch.setattr(client,'ensure_provider_ready',ready)
    client.last_response_metadata={'usage':{'input_tokens':999}}
    async def consume():
        return [part async for part in client.stream_chat(model=LLMModelTarget(provider='test',name='test'),messages=[{'role':'user','content':'Hello'}])]
    try:
        if completed:
            assert await consume()==['hello']
        else:
            with pytest.raises(RuntimeError,match='interrupted'):
                await consume()
        assert client.last_response_metadata=={}
    finally:
        await client.aclose()

@pytest.mark.asyncio
async def test_immediate_dispatch_failure_leaves_durable_admission_recoverable(monkeypatch,caplog):
    from app.services import companion_dispatch_service as dispatcher
    async def unavailable():
        raise RuntimeError('private broker credentials must not be logged')
    monkeypatch.setattr(dispatcher,'dispatch_once',unavailable)
    await dispatcher.dispatch_after_admission()
    assert 'companion_immediate_dispatch_unavailable' in caplog.text
    assert 'private broker credentials' not in caplog.text


def test_sqlite_timestamps_keep_explicit_utc_on_the_wire():
    from datetime import datetime,timezone,timedelta
    from app.repositories.character_companion_repository import utc_iso
    assert utc_iso(datetime(2025,1,1,12))=='2025-01-01T12:00:00+00:00'
    assert utc_iso(datetime(2025,1,1,12,tzinfo=timezone(timedelta(hours=2))))=='2025-01-01T10:00:00+00:00'


@pytest.mark.asyncio
async def test_provider_web_search_uses_annotations_as_evidence_and_never_generated_summary(monkeypatch):
    from app.integrations.companion import retrieval
    from app.integrations.llm.shreckllm_client import ShreckLLMClient
    from app.core.config_store import LLMModelTarget
    target = LLMModelTarget(provider='openrouter', name='search-model', web_search_enabled=True)
    settings = SimpleNamespace(model_companion_synthesis=target, companion_web_enabled=False,
                               shreckllm_base_url='http://gateway', companion_tool_timeout_s=1)
    monkeypatch.setattr(retrieval, 'get_settings', lambda: settings)
    calls = []
    async def chat(self, **kwargs):
        calls.append(kwargs)
        return {'text': 'An invented model summary must not become evidence', 'annotations': [
            {'type': 'url_citation', 'url_citation': {'url': 'https://example.org/source', 'title': 'Actual source', 'content': 'Provider source excerpt'}},
            {'type': 'url_citation', 'url_citation': {'url': 'http://127.0.0.1/private', 'content': 'Private'}},
            {'type': 'url_citation', 'url_citation': {'url': 'https://example.org/no-excerpt'}}]}
    async def safe(url):
        return url.startswith('https://example.org')
    monkeypatch.setattr(ShreckLLMClient, 'chat', chat)
    monkeypatch.setattr(retrieval, 'safe_web_url', safe)
    adapter = retrieval.CompanionRetrieval(SimpleNamespace(request={}), None)
    rows = await adapter.web('latest news')
    assert len(rows) == 1
    assert rows[0]['excerpt'] == 'Provider source excerpt'
    assert rows[0]['epistemic_category'] == 'external_web'
    assert calls[0]['model'].web_search_enabled is True
    assert calls[0]['return_metadata'] is True
    assert retrieval.web_available(settings)
    settings.model_companion_synthesis = target.model_copy(update={'web_search_enabled': False})
    assert not retrieval.web_available(settings)
    assert await adapter.web('latest news') == []


def test_turn_web_denial_fences_optional_role_settings():
    from app.core.config_store import LLMModelTarget
    from app.jobs.companion.runner import turn_target
    target = LLMModelTarget(provider='openrouter', name='m', web_search_enabled=True)
    turn = SimpleNamespace(request={'tools': {'web': 'disabled'}})
    assert turn_target(target, turn).web_search_enabled is False
    assert target.web_search_enabled is True
    turn.request['tools']['web'] = 'allow'
    assert turn_target(target, turn).web_search_enabled is True
