from __future__ import annotations

import pytest

from app.core.security import create_access_token
from app.models import User, UserRole


async def _create_user(session_maker, role: UserRole, suffix: str) -> tuple[User, dict[str, str]]:
    async with session_maker() as session:
        user = User(
            username=f"{role.value}-{suffix}",
            hashed_password="hashed",
            password="",
            full_name=f"{role.value.title()} User",
            email=f"{role.value}-{suffix}@example.com",
            timezone="UTC",
            role=role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    token = create_access_token(str(user.id), role.value)
    return user, {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_personal_companion_lifecycle(client, session_maker) -> None:
    _, headers = await _create_user(session_maker, UserRole.PLAYER, "companion-life")

    create_response = await client.post(
        "/users/me/companion",
        headers=headers,
        json={
            "name": "Echo",
            "writing_style": "Warm and concise",
            "active": True,
        },
    )
    assert create_response.status_code == 201, create_response.text
    created = create_response.json()
    assert created["name"] == "Echo"

    get_response = await client.get("/users/me/companion", headers=headers)
    assert get_response.status_code == 200, get_response.text
    assert get_response.json()["id"] == created["id"]

    patch_response = await client.patch(
        "/users/me/companion",
        headers=headers,
        json={"writing_style": "Reflective and playful", "active": False},
    )
    assert patch_response.status_code == 200, patch_response.text
    patched = patch_response.json()
    assert patched["writing_style"] == "Reflective and playful"
    assert patched["active"] is False

    delete_response = await client.delete("/users/me/companion", headers=headers)
    assert delete_response.status_code == 204, delete_response.text

    missing_response = await client.get("/users/me/companion", headers=headers)
    assert missing_response.status_code == 404, missing_response.text


@pytest.mark.asyncio
async def test_personal_companion_duplicate_create_returns_conflict(client, session_maker) -> None:
    _, headers = await _create_user(session_maker, UserRole.PLAYER, "companion-dup")

    first = await client.post(
        "/users/me/companion",
        headers=headers,
        json={"name": "Echo", "writing_style": "Calm and clear"},
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        "/users/me/companion",
        headers=headers,
        json={"name": "Echo 2", "writing_style": "Different style"},
    )
    assert second.status_code == 409, second.text


@pytest.mark.asyncio
async def test_personal_companion_is_user_scoped(client, session_maker) -> None:
    _, headers_a = await _create_user(session_maker, UserRole.PLAYER, "companion-a")
    _, headers_b = await _create_user(session_maker, UserRole.PLAYER, "companion-b")

    create_response = await client.post(
        "/users/me/companion",
        headers=headers_a,
        json={"name": "A", "writing_style": "A style"},
    )
    assert create_response.status_code == 201, create_response.text

    get_a = await client.get("/users/me/companion", headers=headers_a)
    assert get_a.status_code == 200, get_a.text
    assert get_a.json()["name"] == "A"

    get_b = await client.get("/users/me/companion", headers=headers_b)
    assert get_b.status_code == 404, get_b.text


@pytest.mark.asyncio
async def test_personal_companion_avatar_upload_validation(client, session_maker) -> None:
    _, headers = await _create_user(session_maker, UserRole.PLAYER, "companion-avatar")

    created = await client.post(
        "/users/me/companion",
        headers=headers,
        json={"name": "Sketch", "writing_style": "Friendly"},
    )
    assert created.status_code == 201, created.text

    invalid_upload = await client.post(
        "/users/me/companion/avatar",
        headers=headers,
        files={"file": ("avatar.txt", b"not-an-image", "text/plain")},
    )
    assert invalid_upload.status_code == 400, invalid_upload.text


@pytest.mark.asyncio
async def test_personal_companion_missing_returns_404_for_update_delete_avatar(client, session_maker) -> None:
    _, headers = await _create_user(session_maker, UserRole.PLAYER, "companion-missing")

    patch_response = await client.patch(
        "/users/me/companion",
        headers=headers,
        json={"name": "Any"},
    )
    assert patch_response.status_code == 404, patch_response.text

    delete_response = await client.delete("/users/me/companion", headers=headers)
    assert delete_response.status_code == 404, delete_response.text

    avatar_response = await client.post(
        "/users/me/companion/avatar",
        headers=headers,
        files={"file": ("avatar.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert avatar_response.status_code == 404, avatar_response.text
