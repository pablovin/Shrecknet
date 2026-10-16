from unittest.mock import AsyncMock

import pytest

from shrecknet_client.client import AsyncShrecknetClient


@pytest.mark.asyncio
async def test_public_profiles_sdk_contract():
    async with AsyncShrecknetClient() as client:
        client.raw_request = AsyncMock(
            return_value=[
                {
                    "id": 1,
                    "username": "one",
                    "full_name": "One",
                    "avatar_url": "/media/one.png",
                }
            ]
        )
        profiles = await client.user_summaries([1, 2])
        assert profiles[0].avatar_url == "/media/one.png"
        client.raw_request.assert_awaited_once_with(
            "GET", "/contracts/users", params={"ids": [1, 2]}
        )
        for ids in [[], [0], [True], list(range(1, 102))]:
            with pytest.raises(ValueError):
                await client.user_summaries(ids)
