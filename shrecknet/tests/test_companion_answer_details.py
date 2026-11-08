"""Delivered evidence provenance, budget exclusions and private history projection."""
import pytest
from app.integrations.companion.retrieval import evidence
from app.jobs.companion.contracts import Answer
from app.models.character_companion import CompanionChat, CompanionTurn, CompanionMessage
from app.models.user import User
from app.repositories.character_companion_repository import CompanionRepository
from app.schemas.character_companion import MessageRead


def answer():
    return Answer.model_validate({'segments': [
        {'id': 's1', 'kind': 'fact', 'text': 'The forest is old.', 'evidence_ids': ['atlas:one']},
        {'id': 's2', 'kind': 'opinion', 'text': 'I would visit.', 'evidence_ids': []},
    ], 'coverage': 'complete', 'warnings': []})


def test_tool_details_distinguish_supplied_uncited_and_budget_excluded_evidence():
    rows = [dict(evidence('atlas', name, name), evidence_id=f'atlas:{name}') for name in ('one','two','excluded')]
    result = answer().final(rows[:2], [
        {'tool': 'atlas', 'status': 'ok', 'evidence': rows, 'cache_hit': True, 'elapsed_ms': 0},
        {'tool': 'web', 'status': 'timed_out', 'evidence': []},
        {'tool': 'chronicle', 'status': 'empty', 'evidence': []},
        {'tool': 'shreckworld', 'status': 'unavailable', 'evidence': []},
    ], query='forest')
    call = result['tool_calls'][0]
    assert call == {'tool':'atlas','status':'ok','query':'forest','cache_hit':True,'elapsed_ms':0,'returned_count':3,'evidence_ids':['atlas:one','atlas:two']}
    assert len(result['citations']) == 1
    assert result['evidence'] == rows[:2]
    assert result['coverage'] == 'partial'
    assert result['tool_calls'][1]['elapsed_ms'] is None
    assert 'excluded' not in str(result['evidence'])


@pytest.mark.asyncio
async def test_message_details_are_owner_only_and_legacy_and_user_messages_stay_empty(session_maker):
    async with session_maker() as session:
        user = User(username='details', email='details@example.test', role='player')
        session.add(user)
        await session.flush()
        chat = CompanionChat(user_id=user.id, character_agent_id='fiona', title='Private')
        session.add(chat)
        await session.flush()
        source = dict(evidence('atlas','one','The forest is old.'), evidence_id='atlas:one')
        result = answer().final([source], [{'tool':'atlas','status':'ok','evidence':[source]}])
        turn = CompanionTurn(user_id=user.id, character_agent_id='fiona', chat_id=chat.id, request={}, principal={}, status='completed', result=result)
        session.add(turn)
        await session.flush()
        assistant = CompanionMessage(turn_id=turn.id,chat_id=chat.id,role='assistant',content=result['text'],manifest=result['citations'])
        question = CompanionMessage(turn_id=turn.id,chat_id=chat.id,role='user',content='forest?',manifest=[])
        session.add_all([assistant,question])
        await session.flush()
        repo = CompanionRepository(session)
        records = await repo.message_records([question,assistant],user.id)
        assert records[0]['answer_details'] is None
        parsed = MessageRead.model_validate(records[1])
        assert parsed.answer_details.tool_calls[0].evidence_ids == ['atlas:one']
        assert (await repo.message_records([assistant],user.id+1))[0]['answer_details'] is None
        turn.result = {'text': 'Legacy', 'citations': []}
        assert (await repo.message_records([assistant],user.id))[0]['answer_details'] is None
