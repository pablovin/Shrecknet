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
