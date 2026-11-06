"""Create a Foundry setup key bound to an existing Atlas World as an administrator.

Run with SHRECKNET_BASE_URL, SHRECKNET_ADMIN_TOKEN and SHRECKNET_WORLD_ID.
The raw setup key is displayed once; do not capture stdout in shared logs.
Players continue to use their own Shrecknet accounts for content.
"""
import asyncio
import os

from shrecknet_client import Shrecknet


async def main() -> None:
    world_id = os.environ["SHRECKNET_WORLD_ID"]
    async with Shrecknet(
        base_url=os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100"),
        token=os.environ["SHRECKNET_ADMIN_TOKEN"],
    ) as sdk:
        integration = await sdk.raw_request("POST", "/config/integrations/foundry", json={
            "name": os.getenv("SHRECKNET_FOUNDRY_NAME", "Campaign table"),
            "allowed_world_ids": [world_id],
            "default_world_id": world_id,
        })
        print(f"Integration: {integration['id']} · World: {integration['default_world_id']}")
        print(f"One-time Foundry setup key: {integration['key']}")


if __name__ == "__main__":
    asyncio.run(main())
