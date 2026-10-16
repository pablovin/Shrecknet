import httpx
import pytest

from shrecknet_client.browser_auth import BrowserAuthAPI
from shrecknet_client.client import AsyncShrecknetClient


@pytest.mark.asyncio
async def test_browser_csrf_and_cookie_flow_keeps_legacy_client_compatible():
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.path == "/auth/csrf":
            return httpx.Response(200, json={"csrf_token": "context"}, headers={"set-cookie": "csrf=context; Path=/; HttpOnly"})
        if request.url.path == "/auth/logout":
            assert request.headers["origin"] == "https://os.example.com"
            assert request.headers["x-csrf-token"] == "context"
            assert "csrf=context" in request.headers["cookie"]
            return httpx.Response(204)
        raise AssertionError(request.url)
    client = AsyncShrecknetClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://os.example.com", transport=httpx.MockTransport(handle))
    client.set_token("previous")
    await BrowserAuthAPI(client, "https://os.example.com").logout()
    assert client.token is None
    assert len(calls) == 2
    await client.aclose()
