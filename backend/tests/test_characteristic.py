import pytest

SYSTEM_ADMIN = {
    "nickname": "sysadmin",
    "email": "sysadmin2@example.com",
    "password": "secret123",
    "role": "system admin",
    "image_url": "no image"
}
WRITER = {
    "nickname": "writer",
    "email": "writer2@example.com",
    "password": "secret123",
    "role": "writer",
    "image_url": "no image"
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
async def test_characteristic_create_rbac(async_client):
    sysadmin_token = await register_and_login(async_client, SYSTEM_ADMIN)
    writer_token = await register_and_login(async_client, WRITER)

    # create a gameworld for the characteristic
    gw_payload = {"name": "World", "system": "dnd", "description": "", "logo": ""}
    resp = await async_client.post(
        "/gameworlds/",
        json=gw_payload,
        headers={"Authorization": f"Bearer {sysadmin_token}"},
    )
    assert resp.status_code == 200, resp.text
    gw_id = resp.json()["id"]

    char_payload = {"name": "discipline", "type": "string", "gameworld_id": gw_id}

    # Only system_admin can create
    resp = await async_client.post("/characteristics/", json=char_payload, headers={"Authorization": f"Bearer {sysadmin_token}"})
    assert resp.status_code == 200
    char_id = resp.json()["id"]

    resp = await async_client.post("/characteristics/", json=char_payload, headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 403

    # List characteristics (any role)
    resp = await async_client.get(
        f"/characteristics/?gameworld_id={gw_id}",
        headers={"Authorization": f"Bearer {writer_token}"},
    )
    assert resp.status_code == 200
    assert any(c["id"] == char_id for c in resp.json())

    # Update (system admin only)
    update = {"type": "int"}
    resp = await async_client.patch(f"/characteristics/{char_id}", json=update, headers={"Authorization": f"Bearer {sysadmin_token}"})
    assert resp.status_code == 200
    assert resp.json()["type"] == "int"

    # Writer cannot update
    resp = await async_client.patch(f"/characteristics/{char_id}", json=update, headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 403

    # Delete (system admin only)
    resp = await async_client.delete(f"/characteristics/{char_id}", headers={"Authorization": f"Bearer {sysadmin_token}"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
