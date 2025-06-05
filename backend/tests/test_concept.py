import pytest

SYSTEM_ADMIN = {
    "nickname": "sysadmin",
    "email": "sysadmin@example.com",
    "password": "secret123",
    "role": "system admin",
    "image_url": "no image"
}
WORLD_BUILDER = {
    "nickname": "builder",
    "email": "wb@example.com",
    "password": "secret123",
    "role": "world builder",
    "image_url":"no image"
}
WRITER = {
    "nickname": "writer",
    "email": "writer1@example.com",
    "password": "secret123",
    "role": "writer",
    "image_url":"no image"
}
PLAYER = {
    "nickname": "player",
    "email": "player1@example.com",
    "password": "secret123",
    "role": "player",
    "image_url":"no image"
}

@pytest.mark.anyio
async def register_and_login(async_client, user_data):
    resp = await async_client.post("/users/", json=user_data)
    assert resp.status_code == 200, resp.text
    resp = await async_client.post("/users/login", data={
        "username": user_data["email"], "password": user_data["password"]
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]

@pytest.mark.anyio
async def test_concept_create_update_link_rbac(async_client):
    sysadmin_token = await register_and_login(async_client, SYSTEM_ADMIN)
    wb_token = await register_and_login(async_client, WORLD_BUILDER)
    writer_token = await register_and_login(async_client, WRITER)
    player_token = await register_and_login(async_client, PLAYER)

    # Create a gameworld (system admin or world builder)
    gw_payload = {"name": "Forgotten Realms", "system":"dnd", "description":"Not much!", "logo":"Logo url"}
    resp = await async_client.post("/gameworlds/", json=gw_payload, headers={"Authorization": f"Bearer {wb_token}"})
    print(f"Resp: {resp}")
    assert resp.status_code == 200
    gw_id = resp.json()["id"]

    # Create characteristics
    char1 = {"name": "clan", "type": "string", "gameworld_id": gw_id}
    char2 = {"name": "age", "type": "int", "gameworld_id": gw_id}
    resp = await async_client.post(
        "/characteristics/", json=char1, headers={"Authorization": f"Bearer {sysadmin_token}"}
    )
    assert resp.status_code == 200
    char1_id = resp.json()["id"]
    resp = await async_client.post(
        "/characteristics/",
        json=char2,
        headers={"Authorization": f"Bearer {sysadmin_token}"},
    )
    char2_id = resp.json()["id"]

    # Only system_admin can create concept
    concept_payload = {
        "gameworld_id": gw_id,
        "name": "Player",
        "description": "A player character",
        "characteristic_links": [
            {"characteristic_id": char1_id, "order": 0, "display_type": "header"},
            {"characteristic_id": char2_id, "order": 1, "display_type": "header"},
        ],
    }
    resp = await async_client.post("/concepts/", json=concept_payload, headers={"Authorization": f"Bearer {sysadmin_token}"})
    assert resp.status_code == 200
    concept_id = resp.json()["id"]
    assert {c['id'] for c in resp.json()["characteristics"]} == {char1_id, char2_id}

    # Writer and player cannot create concept
    for token in (writer_token, player_token):
        resp = await async_client.post(
            "/concepts/", json=concept_payload, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403

    # List and get concepts (any role)
    resp = await async_client.get(f"/concepts/{concept_id}", headers={"Authorization": f"Bearer {player_token}"})
    assert resp.status_code == 200
    assert resp.json()["id"] == concept_id

    # Update concept (system admin only)
       
    update = {
        "description": "An updated description",
        "characteristic_links": [
            {"characteristic_id": char1_id, "order": 0, "display_type": "header"}
        ],
    }
    resp = await async_client.patch(f"/concepts/{concept_id}", json=update, headers={"Authorization": f"Bearer {sysadmin_token}"})
    assert resp.status_code == 200
    assert resp.json()["description"] == "An updated description"
        

    assert {c['id'] for c in resp.json()["characteristics"]} == {char1_id}

    # Writer and player cannot update
    for token in (writer_token, player_token):
        resp = await async_client.patch(f"/concepts/{concept_id}", json=update, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    # Link and unlink characteristic (system admin only)
    link_payload = {"concept_id": concept_id, "characteristic_id": char2_id, "order": 2}
    resp = await async_client.post("/characteristics/link/", params=link_payload, headers={"Authorization": f"Bearer {sysadmin_token}"})
    assert resp.status_code == 200
    resp = await async_client.delete("/characteristics/link/", params={"concept_id": concept_id, "characteristic_id": char2_id}, headers={"Authorization": f"Bearer {sysadmin_token}"})
    assert resp.status_code == 200

    # Delete concept (system admin only)
    resp = await async_client.delete(f"/concepts/{concept_id}", headers={"Authorization": f"Bearer {sysadmin_token}"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
