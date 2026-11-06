import pytest

from shrecknet_client.client import AsyncShrecknetClient
from shrecknet_client.errors import AuthenticationError, ValidationError, raise_for_status


@pytest.mark.asyncio
async def test_auth_header_injection() -> None:
    client = AsyncShrecknetClient(token="abc")
    assert client._headers()["Authorization"] == "Bearer abc"
    await client.aclose()


def test_error_translation() -> None:
    with pytest.raises(AuthenticationError):
        raise_for_status(401, "bad token")
    with pytest.raises(ValidationError):
        raise_for_status(422, "invalid")


@pytest.mark.asyncio
async def test_raw_foundry_configuration_preserves_default_and_maps_validation_errors() -> None:
    import httpx

    def respond(request: httpx.Request) -> httpx.Response:
        import json
        assert request.headers["Authorization"] == "Bearer admin"
        payload = json.loads(request.content)
        if payload["default_world_id"] == "outside-scope":
            return httpx.Response(422, json={"detail": "Default World must belong to the integration's allowed Worlds"})
        return httpx.Response(201, json={"id": "table", **payload})

    client = AsyncShrecknetClient(token="admin")
    await client.aclose()
    client._client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(respond))
    async with client:
        created = await client.raw_request("POST", "/config/integrations/foundry", json={
            "name": "Table", "allowed_world_ids": ["a"], "default_world_id": "a",
        })
        assert created["default_world_id"] == "a"
        with pytest.raises(ValidationError):
            await client.raw_request("PUT", "/config/integrations/foundry/table", json={"default_world_id": "outside-scope"})
