import pytest
from datetime import datetime, timezone

WORLD_BUILDER = {
    "nickname": "gm",
    "email": "gm@example.com",
    "password": "secret123",
    "role": "world builder",
    "image_url": "no image",
}


@pytest.mark.anyio
async def test_delete_table_removes_sessions(async_client):
    resp = await async_client.post("/user/", json=WORLD_BUILDER)
    assert resp.status_code == 200, resp.text
    login = await async_client.post(
        "/user/login",
        data={
            "username": WORLD_BUILDER["email"],
            "password": WORLD_BUILDER["password"],
        },
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    gw_payload = {"name": "TestWorld", "system": "dnd", "description": "", "logo": ""}
    resp = await async_client.post("/gameworlds/", json=gw_payload, headers=headers)
    assert resp.status_code == 200, resp.text
    world_id = resp.json()["id"]

    resp = await async_client.post(
        "/tables/",
        json={"world_id": world_id, "name": "Party", "member_ids": []},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    table_id = resp.json()["id"]

    sess_payload = {
        "name": "First",
        "scheduled_time": datetime.now(timezone.utc).isoformat(),
        "summary": "",
        "location": "",
        "table_id": table_id,
        "attendee_ids": [],
        "page_ids": [],
    }
    resp = await async_client.post(
        f"/tables/{table_id}/sessions", json=sess_payload, headers=headers
    )
    assert resp.status_code == 200, resp.text

    resp = await async_client.get(f"/tables/{table_id}/sessions", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = await async_client.delete(f"/tables/{table_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    resp = await async_client.get(f"/tables/{table_id}/sessions", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []
