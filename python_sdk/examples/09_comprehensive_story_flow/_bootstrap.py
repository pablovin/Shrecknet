import json
import os
from pathlib import Path

from shrecknet_client import ConflictError, Shrecknet

STATE_FILE = Path(os.getenv("SHRECKNET_EXAMPLE_STATE_FILE", "python_sdk/examples/09_comprehensive_story_flow/.state.json"))


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"{name} is required")
    return str(value)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


async def ensure_user_and_login(sdk: Shrecknet) -> None:
    username = env("SHRECKNET_USERNAME", "keeper")
    password = env("SHRECKNET_PASSWORD", "change-me-strong")
    email = env("SHRECKNET_EMAIL", "keeper@example.com")
    full_name = env("SHRECKNET_FULL_NAME", "World Keeper")
    timezone = env("SHRECKNET_TIMEZONE", "UTC")

    status = await sdk.client.bootstrap_status()
    if not status.has_users:
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
        except ConflictError:
            pass

    await sdk.login(username, password)
