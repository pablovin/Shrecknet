from __future__ import annotations

import pytest

from app.models.user import UserRole


@pytest.mark.asyncio
async def test_user_registration_and_self_update(client):
    payload = {
        "username": "writer1",
        "password": "WriterPass123",
        "full_name": "Writer One",
        "email": "writer1@example.com",
        "timezone": "UTC",
        "role": UserRole.WRITER.value,
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

    me_response = await client.get("/users/me", headers=headers)
    assert me_response.status_code == 200
    assert me_response.json()["username"] == payload["username"]

    update_response = await client.put(
        f"/users/{user_id}", json={"full_name": "Updated Writer"}, headers=headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["full_name"] == "Updated Writer"


@pytest.mark.asyncio
async def test_registration_enforces_uniqueness(client):
    first_user = {
        "username": "unique1",
        "password": "UniquePass123",
        "full_name": "Unique User",
        "email": "unique@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    second_user = {
        "username": "unique2",
        "password": "UniquePass456",
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
    player_payload = {
        "username": "player1",
        "password": "PlayerPass123",
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

    response = await client.get("/ontologies/", headers=headers)
    assert response.status_code == 403

    no_auth_response = await client.get("/ontologies/")
    assert no_auth_response.status_code == 401
