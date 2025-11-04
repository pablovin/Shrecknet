from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.core.config import get_settings
from app.models.audit import AuditAction, AuditActorType, AuditEntityType
from app.models.user import UserRole


@pytest.mark.asyncio
async def test_user_registration_and_self_update(client):
    payload = {
        "username": "writer1",
        "password": "Writer1",
        "full_name": "Writer One",
        "email": "writer1@example.com",
        "timezone": "UTC",
        "role": UserRole.WRITER.value,
    }
    register_response = await client.post("/users/", json=payload)
    assert register_response.status_code == 201, register_response.text
    user_id = register_response.json()["id"]
    assert register_response.json()["role"] == UserRole.ADMIN.value

    token_response = await client.post(
        "/auth/token",
        data={"username": payload["username"], "password": payload["password"]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200, token_response.text
    token = token_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me_response = await client.get("/users/me", headers=headers)
    assert me_response.status_code == 200
    assert me_response.json()["username"] == payload["username"]

    update_response = await client.put(
        f"/users/{user_id}",
        json={"full_name": "Updated Writer"},
        headers=headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["full_name"] == "Updated Writer"

    logs_response = await client.get("/logs/", headers=headers)
    assert logs_response.status_code == 200

    logs = logs_response.json()
    assert any(
        log["entity_type"] == AuditEntityType.USER.value
        and log["actor_type"] == AuditActorType.USER.value
        and log["action"] == AuditAction.CREATE.value
        for log in logs
    )


@pytest.mark.asyncio
async def test_user_availability_endpoint(client):
    availability_response = await client.get(
        "/users/availability",
        params={"username": "newuser", "email": "new@example.com"},
    )
    assert availability_response.status_code == 200
    assert availability_response.json() == {
        "username_available": True,
        "email_available": True,
    }

    await client.post(
        "/users/",
        json={
            "username": "newuser",
            "password": "Strong1",
            "full_name": "New User",
            "email": "new@example.com",
            "timezone": "UTC",
            "role": UserRole.PLAYER.value,
        },
    )

    availability_after = await client.get(
        "/users/availability",
        params={"username": "newuser", "email": "new@example.com"},
    )
    assert availability_after.status_code == 200
    assert availability_after.json() == {
        "username_available": False,
        "email_available": False,
    }

    missing_params = await client.get("/users/availability")
    assert missing_params.status_code == 400


@pytest.mark.asyncio
async def test_registration_enforces_uniqueness(client):
    first_user = {
        "username": "unique1",
        "password": "Unique1",
        "full_name": "Unique User",
        "email": "unique@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    second_user = {
        "username": "unique2",
        "password": "Unique2",
        "full_name": "Unique User 2",
        "email": "unique@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }

    response1 = await client.post("/users/", json=first_user)
    assert response1.status_code == 201, response1.text

    response2 = await client.post("/users/", json=second_user)
    assert response2.status_code == 409
    assert "Email already exists" in response2.text


@pytest.mark.asyncio
async def test_ontology_endpoints_require_privileged_roles(client):
    """
    Test role-based access control with hierarchical checking.
    
    Player: Can READ ontologies (read-only access)
    Player: CANNOT access admin endpoints like /logs/
    """
    await client.post(
        "/users/",
        json={
            "username": "bootstrap",
            "password": "Boot12",
            "full_name": "Bootstrap Admin",
            "email": "bootstrap@example.com",
            "timezone": "UTC",
            "role": UserRole.ADMIN.value,
        },
    )

    player_payload = {
        "username": "player1",
        "password": "Player1",
        "full_name": "Player One",
        "email": "player1@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    await client.post("/users/", json=player_payload)

    token_response = await client.post(
        "/auth/token",
        data={
            "username": player_payload["username"],
            "password": player_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200, token_response.text
    token = token_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Player CAN read ontologies (read-only access as per role hierarchy)
    response = await client.get("/ontologies/", headers=headers)
    assert response.status_code == 200

    # Unauthenticated users cannot access
    no_auth_response = await client.get("/ontologies/")
    assert no_auth_response.status_code == 401

    # Player CANNOT access admin-only endpoints like audit logs
    logs_response = await client.get("/logs/", headers=headers)
    assert logs_response.status_code == 403


@pytest.mark.asyncio
async def test_long_password_allowed_and_truncated(client):
    long_password = "superlongpassword" * 10  # well over bcrypt limit
    payload = {
        "username": "longpass",
        "password": long_password,
        "full_name": "Long Password User",
        "email": "longpass@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }

    registration = await client.post("/users/", json=payload)
    assert registration.status_code == 201, registration.text

    token_response = await client.post(
        "/auth/token",
        data={"username": payload["username"], "password": long_password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200, token_response.text


@pytest.mark.asyncio
async def test_short_password_rejected(client):
    payload = {
        "username": "shortpass",
        "password": "abc",
        "full_name": "Short Password",
        "email": "shortpass@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }

    response = await client.post("/users/", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_admin_can_delete_user(client):
    admin_payload = {
        "username": "deleter-admin",
        "password": "DeleteAdmin1",
        "full_name": "Delete Admin",
        "email": "deleter-admin@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    admin_register = await client.post("/users/", json=admin_payload)
    assert admin_register.status_code == 201, admin_register.text

    token_response = await client.post(
        "/auth/token",
        data={
            "username": admin_payload["username"],
            "password": admin_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200, token_response.text
    admin_token = token_response.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    user_payload = {
        "username": "victim-user",
        "password": "VictimUser1",
        "full_name": "Victim User",
        "email": "victim@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    user_register = await client.post("/users/", json=user_payload)
    assert user_register.status_code == 201, user_register.text
    victim_id = user_register.json()["id"]

    delete_response = await client.delete(f"/users/{victim_id}", headers=admin_headers)
    assert delete_response.status_code == 204

    get_response = await client.get(f"/users/{victim_id}", headers=admin_headers)
    assert get_response.status_code == 404

    logs_response = await client.get("/logs/", headers=admin_headers)
    assert logs_response.status_code == 200
    logs = logs_response.json()
    assert any(
        log["entity_type"] == AuditEntityType.USER.value
        and log["action"] == AuditAction.DELETE.value
        and log["entity_id"] == victim_id
        for log in logs
    )


@pytest.mark.asyncio
async def test_login_with_email(client):
    """Test that users can login with email instead of username."""
    payload = {
        "username": "email-login-user",
        "password": "EmailLogin123",
        "full_name": "Email Login User",
        "email": "email-login@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    register_response = await client.post("/users/", json=payload)
    assert register_response.status_code == 201, register_response.text

    # Login with email instead of username
    token_response = await client.post(
        "/auth/token",
        data={"username": payload["email"], "password": payload["password"]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200, token_response.text
    token = token_response.json()["access_token"]
    assert token is not None

    # Verify token works
    headers = {"Authorization": f"Bearer {token}"}
    me_response = await client.get("/users/me", headers=headers)
    assert me_response.status_code == 200
    assert me_response.json()["username"] == payload["username"]
    assert me_response.json()["email"] == payload["email"]


@pytest.mark.asyncio
async def test_login_with_username_still_works(client):
    """Test that traditional username login still works."""
    payload = {
        "username": "username-login-user",
        "password": "UsernameLogin123",
        "full_name": "Username Login User",
        "email": "username-login@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    register_response = await client.post("/users/", json=payload)
    assert register_response.status_code == 201, register_response.text

    # Login with username (traditional way)
    token_response = await client.post(
        "/auth/token",
        data={"username": payload["username"], "password": payload["password"]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200, token_response.text
    token = token_response.json()["access_token"]
    assert token is not None

    # Verify token works
    headers = {"Authorization": f"Bearer {token}"}
    me_response = await client.get("/users/me", headers=headers)
    assert me_response.status_code == 200
    assert me_response.json()["username"] == payload["username"]


@pytest.mark.asyncio
async def test_login_with_wrong_email(client):
    """Test that login fails with wrong email."""
    payload = {
        "username": "wrong-email-user",
        "password": "WrongEmail123",
        "full_name": "Wrong Email User",
        "email": "wrong-email@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    register_response = await client.post("/users/", json=payload)
    assert register_response.status_code == 201, register_response.text

    # Try to login with wrong email
    token_response = await client.post(
        "/auth/token",
        data={"username": "nonexistent@example.com", "password": payload["password"]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 401
    assert "Incorrect username/email or password" in token_response.text


@pytest.mark.asyncio
async def test_login_with_wrong_password_for_email(client):
    """Test that login fails with wrong password when using email."""
    payload = {
        "username": "wrong-pass-email-user",
        "password": "CorrectPassword123",
        "full_name": "Wrong Password Email User",
        "email": "wrong-pass-email@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    register_response = await client.post("/users/", json=payload)
    assert register_response.status_code == 201, register_response.text

    # Try to login with email but wrong password
    token_response = await client.post(
        "/auth/token",
        data={"username": payload["email"], "password": "WrongPassword123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 401
    assert "Incorrect username/email or password" in token_response.text


@pytest.mark.asyncio
async def test_user_can_upload_avatar(client):
    payload = {
        "username": "avatar-user",
        "password": "Avatar123",
        "full_name": "Avatar User",
        "email": "avatar@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    register_response = await client.post("/users/", json=payload)
    assert register_response.status_code == 201, register_response.text
    user_id = register_response.json()["id"]

    token_response = await client.post(
        "/auth/token",
        data={"username": payload["username"], "password": payload["password"]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200, token_response.text
    token = token_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    image = Image.new("RGB", (200, 200), color="blue")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)

    avatar_response = await client.post(
        f"/users/{user_id}/avatar",
        headers=headers,
        files={"file": ("avatar.png", buffer, "image/png")},
    )
    assert avatar_response.status_code == 200, avatar_response.text
    data = avatar_response.json()
    settings = get_settings()
    base_url = (
        settings.media_public_url.rstrip("/")
        if settings.media_public_url
        else settings.media_base_url.rstrip("/")
    )
    assert data["avatar_url"] == f"{base_url}/avatars/user_avatar-user.png"
