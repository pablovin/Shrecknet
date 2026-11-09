"""Delivered evidence provenance, budget exclusions and private history projection."""
import pytest
from app.integrations.companion.retrieval import evidence
from app.jobs.companion.contracts import build_answer
from app.models.character_companion import CompanionChat, CompanionTurn, CompanionMessage
from app.models.user import User
from app.repositories.character_companion_repository import CompanionRepository
from app.schemas.character_companion import MessageRead


def test_tool_details_distinguish_supplied_uncited_and_budget_excluded_evidence():
    rows = [dict(evidence('atlas', name, name), evidence_id=f'atlas:{name}') for name in ('one','two','excluded')]
    result = build_answer('The forest is old.[[source:1]] I would visit.', rows[:2], [
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
        result = build_answer('The forest is old.[[source:1]]', [source], [{'tool':'atlas','status':'ok','evidence':[source]}])
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


def test_stored_answer_details_keep_additive_deadline_and_depth_diagnostics():
    from app.schemas.character_companion import AnswerDetailsRead
    result=build_answer('No verified result.',[],[{
        'tool':'shreckworld','status':'timed_out','evidence':[],'retrieval_depth':'deep',
        'timeout_s':600,'timeout_layer':'tool'}])
    call=AnswerDetailsRead.model_validate(result).tool_calls[0]
    assert call.retrieval_depth=='deep' and call.timeout_s==600 and call.timeout_layer=='tool'
