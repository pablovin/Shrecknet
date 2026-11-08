"""Gateway identity delegation fails closed without misreporting dependency outages."""
from types import SimpleNamespace
import httpx
import pytest
from fastapi import Depends, FastAPI
from app import auth


@pytest.fixture
def identity(monkeypatch):
    state=SimpleNamespace(client=httpx.AsyncClient,response=httpx.Response(200,json={'id':7,'role':'admin'}),error=None,calls=[],options=[])
    original=httpx.AsyncClient
    def handle(request):
        state.calls.append(request)
        if state.error:
            raise state.error('identity connection unavailable',request=request)
        return state.response
    def client(**options):
        state.options.append(options)
        return original(transport=httpx.MockTransport(handle),**options)
    monkeypatch.setattr(auth.httpx,'AsyncClient',client)
    monkeypatch.setattr(auth,'get_settings',lambda:SimpleNamespace(shrecknet_api_base_url='http://shrecknet_api:8100/',internal_service_token='internal-only'))
    return state


@pytest.mark.asyncio
async def test_identity_uses_direct_service_auth_without_proxy_or_redirect(identity):
    assert await auth._fetch_shrecknet_user('private-token')=={'id':7,'role':'admin'}
    assert str(identity.calls[0].url)=='http://shrecknet_api:8100/users/me'
    assert identity.calls[0].headers['Authorization']=='Bearer private-token'
    assert identity.options[0]['trust_env'] is False
    assert identity.options[0]['follow_redirects'] is False
    assert identity.options[0]['timeout']==10


@pytest.mark.asyncio
@pytest.mark.parametrize('error',[httpx.ConnectTimeout,httpx.ReadTimeout,httpx.ConnectError,httpx.RemoteProtocolError])
async def test_identity_transport_failure_returns_retryable_503(identity,error):
    identity.error=error
    with pytest.raises(auth.HTTPException) as raised:
        await auth._fetch_shrecknet_user('private-token')
    assert raised.value.status_code==503
    assert 'authentication service' in raised.value.detail
    assert 'private-token' not in raised.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize('code,expected',[(401,401),(403,401),(429,503),(500,503),(503,503),(302,503)])
async def test_identity_rejection_is_distinct_from_unavailability(identity,code,expected):
    identity.response=httpx.Response(code,json={'detail':'upstream details'},headers={'location':'https://other.test'})
    with pytest.raises(auth.HTTPException) as raised:
        await auth._fetch_shrecknet_user('private-token')
    assert raised.value.status_code==expected
    assert len(identity.calls)==1
    assert 'upstream details' not in raised.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize('response',[httpx.Response(200,text='not JSON'),httpx.Response(200,json=[]),httpx.Response(200,json=None)])
async def test_invalid_identity_payload_returns_503(identity,response):
    identity.response=response
    with pytest.raises(auth.HTTPException) as raised:
        await auth._fetch_shrecknet_user('private-token')
    assert raised.value.status_code==503


@pytest.mark.asyncio
async def test_provider_validation_does_not_run_when_identity_times_out(identity):
    app=FastAPI();validated=[]
    @app.get('/providers/openrouter/validate')
    async def validate(user=Depends(auth.get_admin_or_world_builder)):
        validated.append(user)
        return {'valid':True}
    identity.error=httpx.ConnectTimeout
    # Only outbound identity HTTP is mocked; exercise FastAPI dependency handling.
    async with identity.client(transport=httpx.ASGITransport(app=app),base_url='http://gateway') as client:
        response=await client.get('/providers/openrouter/validate',headers={'Authorization':'Bearer private-token'})
    assert response.status_code==503
    assert 'authentication service' in response.json()['detail']
    assert validated==[]


@pytest.mark.asyncio
async def test_delegated_reader_still_cannot_validate_providers(identity):
    identity.response=httpx.Response(200,json={'id':7,'role':'player'})
    user=await auth._fetch_shrecknet_user('private-token')
    with pytest.raises(auth.HTTPException) as raised:
        await auth.get_admin_or_world_builder(user)
    assert raised.value.status_code==403
