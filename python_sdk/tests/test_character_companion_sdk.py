import json
import httpx
import pytest
from shrecknet_client.client import AsyncShrecknetClient
from shrecknet_client.character_companion import CharacterCompanionAPI, CompanionModelConfigUpdate


@pytest.mark.asyncio
async def test_action_keys_are_forwarded_and_stream_frames_are_parsed():
    calls=[]
    def handler(req):
        calls.append(req)
        if req.url.path.endswith('/events'):
            event={'version':1,'event_id':'x','occurred_at':'2026-10-20T12:00:00Z','chat_id':'chat','turn_id':'turn','payload':{'text':'Hello'}}
            return httpx.Response(200,text='event: response.final\ndata: '+json.dumps(event)+'\n\n')
        return httpx.Response(202,json={'turn_id':'turn','chat_id':'chat','status':'queued'})
    client=AsyncShrecknetClient('http://test',token='token')
    await client._client.aclose()
    client._client=httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(handler))
    api=CharacterCompanionAPI(client)
    result=await api.send('chat','Hello',3,idempotency_key='same-action')
    assert result.turn_id=='turn' and calls[-1].headers['Idempotency-Key']=='same-action'
    frames=[item async for item in api.events('turn',after='previous')]
    assert frames[0][0]=='response.final' and frames[0][1].payload['text']=='Hello'
    assert calls[-1].headers['Last-Event-ID']=='previous'
    await client.aclose()


def test_model_update_is_partial_and_rejects_unknown_roles():
    assert CompanionModelConfigUpdate().model_dump(exclude_none=True)=={}
    with pytest.raises(ValueError):CompanionModelConfigUpdate(model_character_agent_decision_making={})

@pytest.mark.asyncio
async def test_private_attachment_bytes_use_auth_and_preserve_http_errors():
    from shrecknet_client.errors import NotFoundError
    calls=[]
    def handler(request):
        calls.append(request)
        if 'missing' in request.url.path:
            return httpx.Response(404,json={'detail':'attachment_not_found'})
        if request.url.path.endswith('/content'):
            return httpx.Response(200,content=b'normalized-image',headers={'content-type':'image/png'})
        return httpx.Response(200,json={'id':'image','content_url':'/users/me/character-companion/attachments/image/content'})
    client=AsyncShrecknetClient('http://test',token='private-token')
    await client._client.aclose()
    client._client=httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(handler))
    try:
        api=CharacterCompanionAPI(client)
        assert (await api.attachment('image'))['id']=='image'
        assert await api.attachment_content('image')==b'normalized-image'
        with pytest.raises(NotFoundError):await api.attachment_content('missing')
        assert all(call.headers['Authorization']=='Bearer private-token' for call in calls)
    finally:await client.aclose()


@pytest.mark.asyncio
async def test_foundry_snapshot_forwarded_without_changing_table_identifiers():
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(202, json={"turn_id": "turn", "chat_id": "chat", "status": "queued"})
    client = AsyncShrecknetClient('http://test', token='private-token')
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url='http://test', transport=httpx.MockTransport(handler))
    value = {"version": 1, "captured_at": "2026-11-06T12:00:00Z", "table": None, "documents": []}
    try:
        await CharacterCompanionAPI(client).send('chat', 'Hello', 1, idempotency_key='same', foundry_context=value)
        assert calls[-1]['foundry_context'] == value
        await CharacterCompanionAPI(client).send('chat', 'Hello', 1, idempotency_key='other')
        assert 'foundry_context' not in calls[-1]
    finally:
        await client.aclose()

@pytest.mark.asyncio
async def test_answer_breakdown_survives_turn_and_message_reads():
    details = {'tool_calls': [{'tool':'atlas','status':'ok','query':'forest','cache_hit':False,'elapsed_ms':4,'returned_count':1,'evidence_ids':['e1']}], 'evidence':[{'evidence_id':'e1','excerpt':'Old trees'}], 'segments':[{'id':'s1','text':'Forest','kind':'fact','evidence_ids':['e1']}], 'coverage':'complete','warnings':[]}
    def handler(request):
        if request.url.path.endswith('/messages'):
            return httpx.Response(200,json={'items':[{'id':'message','answer_details':details}], 'next_cursor':None})
        return httpx.Response(200,json={'id':'turn','chat_id':'chat','status':'completed','result':{'text':'Forest',**details}})
    client=AsyncShrecknetClient('http://test',token='private-token')
    await client._client.aclose()
    client._client=httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(handler))
    try:
        api=CharacterCompanionAPI(client)
        assert (await api.turn('turn')).result['tool_calls']==details['tool_calls']
        assert (await api.messages('chat'))['items'][0]['answer_details']==details
    finally:
        await client.aclose()
