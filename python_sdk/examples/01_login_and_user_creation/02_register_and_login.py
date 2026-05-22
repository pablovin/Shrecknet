import asyncio
import os
from importlib import import_module

from shrecknet_client import Shrecknet

ensure_user_and_login = import_module("python_sdk.examples.01_login_and_user_creation.00_user_registration").ensure_user_and_login


# Purpose:
# - Demonstrate first-user bootstrap rule and reusable login flow.
# Expected result:
# - Creates first admin user when DB is empty, then prints authenticated profile.
async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")

    async with Shrecknet(base_url=base_url) as sdk:
        print("Bootstrap flow: if no users exist, first registered user becomes admin.")
        await ensure_user_and_login(sdk)

        me = await sdk.me()
        print("Authenticated as:", me.model_dump())


if __name__ == "__main__":
    asyncio.run(main())
