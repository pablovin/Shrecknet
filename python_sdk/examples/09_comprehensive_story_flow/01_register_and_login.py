import asyncio
import os

from shrecknet_client import Shrecknet
from python_sdk.examples.09_comprehensive_story_flow._bootstrap import ensure_user_and_login


async def main() -> None:
    async with Shrecknet(base_url=os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")) as sdk:
        await ensure_user_and_login(sdk)
        me = await sdk.me()
        print("authenticated_user:", me.username)


if __name__ == "__main__":
    asyncio.run(main())
