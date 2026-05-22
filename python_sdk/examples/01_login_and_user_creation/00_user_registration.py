import os

from shrecknet_client import Shrecknet
from shrecknet_client.errors import ConflictError


async def ensure_user_and_login(sdk: Shrecknet) -> None:
    username = os.getenv("SHRECKNET_USERNAME", "keeper")
    password = os.getenv("SHRECKNET_PASSWORD", "change-me-strong")
    email = os.getenv("SHRECKNET_EMAIL", "keeper@example.com")
    full_name = os.getenv("SHRECKNET_FULL_NAME", "World Keeper")
    timezone = os.getenv("SHRECKNET_TIMEZONE", "UTC")

    status = await sdk.client.bootstrap_status()
    if not status.has_users:
        print("No users found. Registering first user as admin (bootstrap rule).")
        await sdk.client.register_user(
            username=username,
            password=password,
            email=email,
            full_name=full_name,
            timezone=timezone,
            role="admin",
        )
    else:
        try:
            await sdk.client.register_user(
                username=username,
                password=password,
                email=email,
                full_name=full_name,
                timezone=timezone,
                role="player",
            )
            print("Registered user for this example run.")
        except ConflictError:
            pass

    await sdk.login(username, password)
