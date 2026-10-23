"""Global quota, deletion and title contracts for Companion sessions."""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app.models.user import User
from app.schemas.character_companion import ChatCreate, ChatUpdate
from app.services.character_companion_service import CharacterCompanionService


class Characters:
    async def ensure_queryable(self, *args, **kwargs):
        pass

    async def get_agent(self, identifier, **kwargs):
        return SimpleNamespace(id=identifier, name=identifier, ontology_id=1, status='active', identity_description={'identity_summary':'ready'})


@pytest.mark.asyncio
async def test_global_quota_replay_and_deletion(session_maker):
    async with session_maker() as session:
        user=User(username='quota',email='quota@example.test',role='player')
        session.add(user)
        await session.flush()
        svc=CharacterCompanionService(session, Characters())
        await svc.select(user,'fiona',0)
        payload=ChatCreate(character_agent_id='fiona',selection_version=1)
        first_key=str(uuid4())
        first=await svc.create_chat(user,payload,first_key)
        for _ in range(8):
            await svc.create_chat(user,payload,str(uuid4()))
        await svc.select(user,'shrek',1)
        other=ChatCreate(character_agent_id='shrek',selection_version=2)
        await svc.create_chat(user,other,str(uuid4()))
        assert await svc.chat_usage(user)=={'count':10,'limit':10,'remaining':0}
        assert await svc.create_chat(user,payload,first_key)==first
        with pytest.raises(HTTPException) as error:
            await svc.create_chat(user,other,str(uuid4()))
        assert error.value.detail['code']=='chat_limit_reached'
        await session.rollback()
        # Refresh actor after rollback expires SQLAlchemy instances.
        await session.refresh(user)
        await svc.delete_chat(user,first['id'])
        await svc.delete_chat(user,first['id'])
        assert (await svc.chat_usage(user))['count']==9
        await svc.create_chat(user,other,str(uuid4()))
        assert (await svc.chat_usage(user))['count']==10
        events=await svc.repo.events(user.id)
        assert all(row.chat_id!=first['id'] for row in events)
        assert any(row.type=='chat.deleted' for row in events)


@pytest.mark.asyncio
async def test_busy_chat_requires_cancel_and_foreign_delete_denied(session_maker):
    async with session_maker() as session:
        user=User(username='busy',email='busy@example.test',role='player')
        session.add(user)
        await session.flush()
        svc=CharacterCompanionService(session,Characters())
        await svc.select(user,'fiona',0)
        chat=await svc.create_chat(user,ChatCreate(character_agent_id='fiona',selection_version=1),str(uuid4()))
        row=await svc.chat(user,chat['id'])
        row.active_turn_id=str(uuid4())
        await session.commit()
        with pytest.raises(HTTPException) as error:
            await svc.delete_chat(user,chat['id'])
        assert error.value.detail['code']=='chat_busy'
        await session.rollback()
        with pytest.raises(HTTPException) as error:
            await svc.delete_chat(SimpleNamespace(id=999999),chat['id'])
        assert error.value.status_code==404


def test_titles_trim_and_reject_blank():
    assert ChatCreate(character_agent_id='fiona',selection_version=1,title='  Campfire  ').title=='Campfire'
    assert ChatUpdate(title='  Campfire  ',expected_revision=1).title=='Campfire'
    for cls,extra in [(ChatCreate,{'character_agent_id':'fiona','selection_version':1}),(ChatUpdate,{'expected_revision':1})]:
        with pytest.raises(ValidationError):
            cls(title='   ',**extra)


@pytest.mark.asyncio
async def test_concurrent_tenth_chat_is_atomic(tmp_path):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.db.base import Base
    engine=create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/quota.db')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with sessions() as session:
        user=User(username='race',email='race@example.test',role='player')
        session.add(user)
        await session.flush()
        actor=SimpleNamespace(id=user.id,role='player')
        svc=CharacterCompanionService(session,Characters())
        await svc.select(actor,'fiona',0)
        payload=ChatCreate(character_agent_id='fiona',selection_version=1)
        for _ in range(9):
            await svc.create_chat(actor,payload,str(uuid4()))
    async def create():
        async with sessions() as session:
            try:
                await CharacterCompanionService(session,Characters()).create_chat(actor,payload,str(uuid4()))
                return 'created'
            except HTTPException as error:
                await session.rollback()
                return error.detail['code']
    try:
        assert sorted(await asyncio.gather(create(),create()))==['chat_limit_reached','created']
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_context_keeps_complete_exchanges_and_isolates_chats(session_maker):
    from datetime import datetime, timezone, timedelta
    from app.models.character_companion import CompanionTurn, CompanionMessage
    async with session_maker() as session:
        user=User(username='history',email='history@example.test',role='player')
        session.add(user)
        await session.flush()
        svc=CharacterCompanionService(session,Characters())
        await svc.select(user,'fiona',0)
        payload=ChatCreate(character_agent_id='fiona',selection_version=1)
        a=await svc.create_chat(user,payload,str(uuid4()))
        b=await svc.create_chat(user,payload,str(uuid4()))
        for index,(chat,status,text) in enumerate([(a,'completed','Dawn'+('x'*1500)),(b,'completed','Secret'),(a,'failed','Failed')]):
            turn=CompanionTurn(chat_id=chat['id'],user_id=user.id,character_agent_id='fiona',request={},principal={},status=status,created_at=datetime.now(timezone.utc)+timedelta(seconds=index))
            session.add(turn)
            await session.flush()
            for role in ['user','assistant']:
                session.add(CompanionMessage(turn_id=turn.id,chat_id=chat['id'],role=role,content=text,committed=status=='completed'))
        await session.commit()
        recent=await svc.repo.conversation_context(a['id'],user.id)
        assert len(recent)==2 and recent[0]['role']=='user'
        assert recent[0]['content']=='Dawn'+('x'*1500)
        assert await svc.repo.conversation_context(a['id'],user.id,max_characters=100)==[]
        assert await svc.repo.conversation_context(a['id'],99999)==[]
