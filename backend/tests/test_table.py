import pytest
from datetime import datetime, timezone, timedelta

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


@pytest.mark.anyio
async def test_list_tables_handles_naive_session_times(async_client):
    user_payload = {
        "nickname": "gm2",
        "email": "gm2@example.com",
        "password": "secret123",
        "role": "world builder",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=user_payload)
    assert resp.status_code == 200, resp.text
    login = await async_client.post(
        "/user/login",
        data={
            "username": user_payload["email"],
            "password": user_payload["password"],
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

    past_time = (datetime.utcnow() - timedelta(days=1)).isoformat()
    future_time = (datetime.utcnow() + timedelta(days=1)).isoformat()

    for sched in (past_time, future_time):
        sess_payload = {
            "name": "Session",
            "scheduled_time": sched,
            "summary": "",
            "location": "",
            "table_id": table_id,
            "timezone": "UTC",
            "attendee_ids": [],
            "page_ids": [],
        }
        resp = await async_client.post(
            f"/tables/{table_id}/sessions", json=sess_payload, headers=headers
        )
        assert resp.status_code == 200, resp.text

    resp = await async_client.get("/tables/", headers=headers)
    assert resp.status_code == 200, resp.text
    tables = resp.json()
    assert len(tables) == 1
    table_info = tables[0]
    assert table_info["latest_session"] is not None
    assert table_info["next_session"] is not None


@pytest.mark.anyio
async def test_list_tables_handles_null_session_times(async_client):
    user_payload = {
        "nickname": "gm3",
        "email": "gm3@example.com",
        "password": "secret123",
        "role": "world builder",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=user_payload)
    assert resp.status_code == 200, resp.text
    login = await async_client.post(
        "/user/login",
        data={
            "username": user_payload["email"],
            "password": user_payload["password"],
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
        "name": "Unscheduled",
        "scheduled_time": None,
        "summary": "",
        "location": "",
        "table_id": table_id,
        "timezone": "UTC",
        "attendee_ids": [],
        "page_ids": [],
    }
    resp = await async_client.post(
        f"/tables/{table_id}/sessions", json=sess_payload, headers=headers
    )
    assert resp.status_code == 200, resp.text

    resp = await async_client.get("/tables/", headers=headers)
    assert resp.status_code == 200, resp.text
    tables = resp.json()
    assert len(tables) == 1
    table_info = tables[0]
    assert table_info["latest_session"] is None
    assert table_info["next_session"] is None
