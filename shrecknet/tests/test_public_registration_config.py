from types import SimpleNamespace

import pytest

from app.api.routers import configurations
from app.core.config_store import UserCreationMode


@pytest.mark.asyncio
async def test_public_registration_config_is_available_without_authentication(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(
        configurations,
        "get_settings",
        lambda: SimpleNamespace(user_creation_mode=UserCreationMode.MODERATED),
    )

    response = await client.get("/config/public")

    assert response.status_code == 200
    assert response.json() == {
        "user_creation_mode": "moderated",
        "email_verification_required": False,
    }
