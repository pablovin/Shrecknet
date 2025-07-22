import pytest

SYSTEM_ADMIN = {
    "nickname": "sysadmin",
    "email": "sysadmin3@example.com",
    "password": "secret123",
    "role": "system admin",
    "image_url": "no image"
}
WRITER = {
    "nickname": "writer",
    "email": "writer3@example.com",
    "password": "secret123",
    "role": "writer",
    "image_url": "no image"
}
PLAYER = {
    "nickname": "player",
    "email": "player3@example.com",
    "password": "secret123",
    "role": "player",
    "image_url": "no image"
}

@pytest.mark.anyio
async def register_and_login(async_client, user_data):
    resp = await async_client.post("/user/", json=user_data)
    if resp.status_code == 400 and "already registered" in resp.text:
        login_resp = await async_client.post(
            "/user/login",
            data={"username": user_data["email"], "password": user_data["password"]},
        )
        assert login_resp.status_code == 200, login_resp.text
        return login_resp.json()["access_token"]
    assert resp.status_code == 200, resp.text
    resp = await async_client.post(
        "/user/login",
        data={"username": user_data["email"], "password": user_data["password"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]

@pytest.mark.anyio
async def test_page_create_update_delete_rbac(async_client):
    sysadmin_token = await register_and_login(async_client, SYSTEM_ADMIN)
    writer_token = await register_and_login(async_client, WRITER)
    player_token = await register_and_login(async_client, PLAYER)

    # Create gameworld
    gw_payload = {"name": "Forgotten Realms", "system":"dnd", "description":"Not much!", "logo":"Logo url"}
    resp = await async_client.post("/gameworlds/", json=gw_payload, headers={"Authorization": f"Bearer {sysadmin_token}"})
    assert resp.status_code == 200
    gw_id = resp.json()["id"]

    # Create concept
    concept_payload = {"gameworld_id": gw_id, "name": "Clan", "description": "Clan"}
    resp = await async_client.post("/concepts/", json=concept_payload, headers={"Authorization": f"Bearer {sysadmin_token}"})
    assert resp.status_code == 200
    concept_id = resp.json()["id"]

    # Create page (writer can create)
    page_payload = {"gameworld_id": gw_id, "concept_id": concept_id, "name": "Ventrue", "content": "Blue Bloods"}
    resp = await async_client.post("/pages/", json=page_payload, headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 200
    page_id = resp.json()["id"]

    # Created page should have changelog entry
    resp = await async_client.get(f"/pages/{page_id}", headers={"Authorization": f"Bearer {player_token}"})
    assert resp.status_code == 200
    assert resp.json()["changelog"][0]["change_type"] == "created"

    # Player cannot create
    resp = await async_client.post("/pages/", json=page_payload, headers={"Authorization": f"Bearer {player_token}"})
    assert resp.status_code == 403

    # Get page (anyone)
    resp = await async_client.get(f"/pages/{page_id}", headers={"Authorization": f"Bearer {player_token}"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Ventrue"

    # Update page (writer can update)
    update = {"content": "Blue Bloods Updated"}
    resp = await async_client.patch(f"/pages/{page_id}", json=update, headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 200
    assert resp.json()["content"] == "Blue Bloods Updated"
    assert resp.json()["changelog"][-1]["change_type"] == "updated"

     # If allowed_user_ids is set, players can edit if they're in the list, writers can always edit

    update2 = {"allowed_user_ids": [999]}
    resp = await async_client.patch(f"/pages/{page_id}", json=update2, headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 200

    # Writer can still edit
    resp = await async_client.patch(f"/pages/{page_id}", json=update, headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 200
    assert resp.json()["content"] == "Blue Bloods Updated"

    # Player NOT in allowed_user_ids cannot edit
    resp = await async_client.patch(f"/pages/{page_id}", json=update, headers={"Authorization": f"Bearer {player_token}"})
    assert resp.status_code == 403

    # Simulate user 999 as a player
    user999 = {
        "nickname": "user999",
        "email": "user999@example.com",
        "password": "secret123",
        "role": "player",
        "image_url": "no image"
    }
    user999_token = await register_and_login(async_client, user999)    


    resp = await async_client.patch(f"/pages/{page_id}", json=update, headers={"Authorization": f"Bearer {user999_token}"})
    assert resp.status_code == 403

    # But the allowed user can (simulate login as user id 999)
    # (Here, you'd register a user with id 999 and test!)

    # Delete page (writer can delete)
    resp = await async_client.delete(f"/pages/{page_id}", headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Player cannot delete
    resp = await async_client.post("/pages/", json=page_payload, headers={"Authorization": f"Bearer {writer_token}"})
    page_id2 = resp.json()["id"]
    resp = await async_client.delete(f"/pages/{page_id2}", headers={"Authorization": f"Bearer {player_token}"})
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_safe_delete_removes_page_refs(async_client):
    sysadmin_token = await register_and_login(async_client, SYSTEM_ADMIN)
    writer_token = await register_and_login(async_client, WRITER)

    # Create world and concept
    gw_payload = {"name": "SafeWorld", "system": "sys", "description": "d", "logo": "logo"}
    resp = await async_client.post("/gameworlds/", json=gw_payload, headers={"Authorization": f"Bearer {sysadmin_token}"})
    assert resp.status_code == 200
    gw_id = resp.json()["id"]

    concept_payload = {"gameworld_id": gw_id, "name": "Clan", "description": "c"}
    resp = await async_client.post("/concepts/", json=concept_payload, headers={"Authorization": f"Bearer {sysadmin_token}"})
    assert resp.status_code == 200
    concept_id = resp.json()["id"]

    # Create page_ref characteristic in this world
    char_payload = {
        "gameworld_id": gw_id,
        "name": "ref",
        "type": "page_ref",
        "ref_concept_id": concept_id,
        "is_list": True,
    }
    resp = await async_client.post("/characteristics/", json=char_payload, headers={"Authorization": f"Bearer {sysadmin_token}"})
    assert resp.status_code == 200
    char_id = resp.json()["id"]

    resp = await async_client.post(
        "/characteristics/link/",
        params={"concept_id": concept_id, "characteristic_id": char_id, "order": 0},
        headers={"Authorization": f"Bearer {sysadmin_token}"},
    )
    assert resp.status_code == 200

    # Create two pages
    page1 = {"gameworld_id": gw_id, "concept_id": concept_id, "name": "P1"}
    resp = await async_client.post("/pages/", json=page1, headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 200
    p1_id = resp.json()["id"]

    page2 = {
        "gameworld_id": gw_id,
        "concept_id": concept_id,
        "name": "P2",
        "values": [{"characteristic_id": char_id, "value": [str(p1_id)]}],
    }
    resp = await async_client.post("/pages/", json=page2, headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 200
    p2_id = resp.json()["id"]

    # Ensure reference is stored
    resp = await async_client.get(f"/pages/{p2_id}", headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 200
    vals = resp.json()["values"]
    assert vals and str(p1_id) in vals[0]["value"]

    # Delete first page
    resp = await async_client.delete(f"/pages/{p1_id}", headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 200

    # Fetch second page again - reference should be removed
    resp = await async_client.get(f"/pages/{p2_id}", headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 200
    vals = resp.json()["values"]
    assert not vals or str(p1_id) not in (vals[0]["value"] or [])
