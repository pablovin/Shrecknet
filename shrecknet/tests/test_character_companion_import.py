import json
import sqlite3
import pytest
from sqlalchemy import select
from app.models.user import User
from app.models.character_companion import CompanionMessage, CompanionRapport, CompanionOutbox
from app.services.companion_import_service import import_companions


@pytest.mark.asyncio
async def test_read_only_import_dry_run_rerun_and_no_historical_rapport(session_maker,tmp_path):
    source=tmp_path/'legacy.db'
    chats=tmp_path/'chats'
    path=chats/'7'/'fiona'/'legacy-session.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'user_id':7,'companion_id':'fiona','messages':[{'role':'user','content':'Hello'},{'role':'assistant','content':'Welcome'}]}))
    with sqlite3.connect(source) as conn:
        conn.executescript("CREATE TABLE user_character_agent_selection(user_id INTEGER,character_agent_id TEXT); INSERT INTO user_character_agent_selection VALUES(7,'fiona'); CREATE TABLE user_character_rapport(user_id INTEGER,character_agent_id TEXT,trust INTEGER); INSERT INTO user_character_rapport VALUES(7,'fiona',31); CREATE TABLE sessions(session_id TEXT,user_id INTEGER,companion_id TEXT,ontology_id INTEGER); INSERT INTO sessions VALUES('legacy-session',7,'fiona',12);")
    original=source.read_bytes()
    async def lookup(user_id,character):return character=='fiona'
    async with session_maker() as session:
        user=User(username='owner',email='owner@example.test',role='player');session.add(user);await session.commit()
        kwargs=dict(source=source,chats_dir=chats,installation='fixture',user_mapping={'7':user.id},character_lookup=lookup)
        preview=await import_companions(session,**kwargs)
        assert preview['imported']==3 and preview['dry_run']
        assert not list((await session.execute(select(CompanionRapport))).scalars())
        with pytest.raises(ValueError,match='reviewed'):
            await import_companions(session,**kwargs,apply=True)
        applied=await import_companions(session,**kwargs,apply=True,ownership_reviewed=True)
        assert applied['imported']==3
        again=await import_companions(session,**kwargs,apply=True,ownership_reviewed=True)
        assert again['already_imported']==3
        assert len(list((await session.execute(select(CompanionMessage))).scalars()))==2
        assert list((await session.execute(select(CompanionOutbox))).scalars())==[]
    assert source.read_bytes()==original

@pytest.mark.asyncio
async def test_unknown_legacy_schema_is_rejected(session_maker,tmp_path):
    source=tmp_path/'unknown.db'
    with sqlite3.connect(source) as connection:
        connection.execute('CREATE TABLE unrelated (id INTEGER)')
    async with session_maker() as session:
        with pytest.raises(ValueError,match='Unsupported legacy schema'):
            await import_companions(session,source,tmp_path,'unknown',{},None)

@pytest.mark.asyncio
async def test_legacy_ts_and_meta_interim_are_preserved(session_maker,tmp_path):
    from app.models.character_companion import CompanionTurn
    source=tmp_path/'legacy.db'
    chats=tmp_path/'chats'
    path=chats/'7'/'fiona'/'history.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'messages':[
        {'role':'user','content':'Hello','ts':'2025-01-01T12:00:00Z'},
        {'role':'assistant','content':'Thinking','ts':'2025-01-01T12:00:01Z','meta':{'message_kind':'interim'}},
        {'role':'assistant','content':'Welcome','ts':'2025-01-01T12:00:02Z'}]}))
    with sqlite3.connect(source) as connection:
        connection.executescript("CREATE TABLE sessions(session_id TEXT,user_id INTEGER,companion_id TEXT,ontology_id INTEGER);INSERT INTO sessions VALUES('history',7,'fiona',12);")
    async def lookup(user_id,character):return True
    async with session_maker() as session:
        user=User(username='importer',email='importer@example.test',role='player')
        session.add(user);await session.commit()
        await import_companions(session,source,chats,'fixture',{'7':user.id},lookup,apply=True,ownership_reviewed=True)
        messages=list((await session.execute(select(CompanionMessage).order_by(CompanionMessage.created_at))).scalars())
        assert [message.role for message in messages]==['user','interim','assistant']
        assert messages[0].created_at.year==2025
        assert not messages[1].committed
        assert not list((await session.execute(select(CompanionOutbox))).scalars())


def test_import_timestamp_preserves_offset_instant():
    from app.services.companion_import_service import timestamp
    assert timestamp('2025-01-01T12:00:00+02:00').isoformat()=='2025-01-01T10:00:00+00:00'
