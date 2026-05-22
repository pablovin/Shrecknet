import asyncio
import os

from shrecknet_client import Shrecknet


# Purpose:
# - Verify credentials work against /auth/token and /users/me.
# Expected result:
# - Prints token type and authenticated user profile.
async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")
    username = os.getenv("SHRECKNET_USERNAME", "keeper")
    password = os.getenv("SHRECKNET_PASSWORD", "change-me-strong")

    async with Shrecknet(base_url=base_url) as sdk:
        # Authenticate with username/email + password.
        token = await sdk.login(username, password)

        # Fetch currently authenticated user details.
        me = await sdk.me()
        print("token_type:", token.token_type)
        print("me:", me.model_dump())


if __name__ == "__main__":
    asyncio.run(main())
