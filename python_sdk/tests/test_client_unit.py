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
