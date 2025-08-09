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
async def test_delete_table_removes_sessions_and_polls(async_client):
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
    session_id = resp.json()["id"]

    poll_payload = {
        "proposed_times": [datetime.now(timezone.utc).isoformat()],
    }
    resp = await async_client.post(
        f"/tables/{table_id}/sessions/{session_id}/poll",
        json=poll_payload,
        headers=headers,
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

    resp = await async_client.get(
        f"/tables/{table_id}/sessions/{session_id}/poll", headers=headers
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_update_table_members(async_client):
    gm_payload = {
        "nickname": "gm_member",
        "email": "gm_member@example.com",
        "password": "secret123",
        "role": "world builder",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=gm_payload)
    assert resp.status_code == 200, resp.text
    gm_id = resp.json()["id"]
    login = await async_client.post(
        "/user/login",
        data={"username": gm_payload["email"], "password": gm_payload["password"]},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    player1 = {
        "nickname": "player1",
        "email": "player1@example.com",
        "password": "secret123",
        "role": "world builder",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=player1)
    player1_id = resp.json()["id"]

    player2 = {
        "nickname": "player2",
        "email": "player2@example.com",
        "password": "secret123",
        "role": "world builder",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=player2)
    player2_id = resp.json()["id"]

    gw_payload = {"name": "World", "system": "dnd", "description": "", "logo": ""}
    resp = await async_client.post("/gameworlds/", json=gw_payload, headers=headers)
    world_id = resp.json()["id"]

    resp = await async_client.post(
        "/tables/",
        json={"world_id": world_id, "name": "Party", "member_ids": [player1_id]},
        headers=headers,
    )
    table_id = resp.json()["id"]

    resp = await async_client.patch(
        f"/tables/{table_id}",
        json={"member_ids": [gm_id, player2_id]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await async_client.get("/tables/", headers=headers)
    members = {m["id"] for m in resp.json()[0]["members"]}
    assert gm_id in members
    assert player2_id in members
    assert player1_id not in members


@pytest.mark.anyio
async def test_added_member_gets_existing_sessions(async_client):
    gm_payload = {
        "nickname": "gm_add",
        "email": "gm_add@example.com",
        "password": "secret123",
        "role": "world builder",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=gm_payload)
    gm_id = resp.json()["id"]
    login = await async_client.post(
        "/user/login",
        data={"username": gm_payload["email"], "password": gm_payload["password"]},
    )
    token = login.json()["access_token"]
    gm_headers = {"Authorization": f"Bearer {token}"}

    player_payload = {
        "nickname": "player_added",
        "email": "player_added@example.com",
        "password": "secret123",
        "role": "world builder",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=player_payload)
    player_id = resp.json()["id"]

    gw_payload = {"name": "World", "system": "dnd", "description": "", "logo": ""}
    resp = await async_client.post("/gameworlds/", json=gw_payload, headers=gm_headers)
    world_id = resp.json()["id"]

    resp = await async_client.post(
        "/tables/",
        json={"world_id": world_id, "name": "Party", "member_ids": []},
        headers=gm_headers,
    )
    table_id = resp.json()["id"]

    sess_payload = {
        "name": "Session",
        "scheduled_time": datetime.now(timezone.utc).isoformat(),
        "summary": "",
        "location": "",
        "table_id": table_id,
        "timezone": "UTC",
        "attendee_ids": [],
        "page_ids": [],
    }
    resp = await async_client.post(
        f"/tables/{table_id}/sessions", json=sess_payload, headers=gm_headers
    )
    session_id = resp.json()["id"]

    poll_payload = {"proposed_times": [datetime.now(timezone.utc).isoformat()]}
    resp = await async_client.post(
        f"/tables/{table_id}/sessions/{session_id}/poll",
        json=poll_payload,
        headers=gm_headers,
    )
    option_id = resp.json()["options"][0]["id"]

    resp = await async_client.patch(
        f"/tables/{table_id}",
        json={"member_ids": [gm_id, player_id]},
        headers=gm_headers,
    )
    assert resp.status_code == 200

    login = await async_client.post(
        "/user/login",
        data={
            "username": player_payload["email"],
            "password": player_payload["password"],
        },
    )
    player_token = login.json()["access_token"]
    player_headers = {"Authorization": f"Bearer {player_token}"}

    resp = await async_client.get(
        f"/tables/{table_id}/sessions?joined=true", headers=player_headers
    )
    assert resp.status_code == 200
    sessions = resp.json()
    assert any(s["id"] == session_id for s in sessions)

    resp = await async_client.post(
        f"/tables/{table_id}/sessions/{session_id}/poll/vote",
        json={"option_ids": [option_id]},
        headers=player_headers,
    )
    assert resp.status_code == 200
    poll = resp.json()
    assert player_id in poll["options"][0]["votes"]


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


@pytest.mark.anyio
async def test_admin_can_list_all_tables(async_client):
    admin_payload = {
        "nickname": "admin",
        "email": "admin@example.com",
        "password": "secret123",
        "role": "system admin",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=admin_payload)
    assert resp.status_code == 200, resp.text
    login_admin = await async_client.post(
        "/user/login",
        data={
            "username": admin_payload["email"],
            "password": admin_payload["password"],
        },
    )
    assert login_admin.status_code == 200, login_admin.text
    admin_token = login_admin.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    gm1 = {
        "nickname": "gm_a",
        "email": "gm_a@example.com",
        "password": "secret123",
        "role": "world builder",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=gm1)
    assert resp.status_code == 200, resp.text
    login_gm1 = await async_client.post(
        "/user/login", data={"username": gm1["email"], "password": gm1["password"]}
    )
    token_gm1 = login_gm1.json()["access_token"]
    headers_gm1 = {"Authorization": f"Bearer {token_gm1}"}

    gm2 = {
        "nickname": "gm_b",
        "email": "gm_b@example.com",
        "password": "secret123",
        "role": "world builder",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=gm2)
    assert resp.status_code == 200, resp.text
    login_gm2 = await async_client.post(
        "/user/login", data={"username": gm2["email"], "password": gm2["password"]}
    )
    token_gm2 = login_gm2.json()["access_token"]
    headers_gm2 = {"Authorization": f"Bearer {token_gm2}"}

    gw_payload = {"name": "WorldA", "system": "dnd", "description": "", "logo": ""}
    resp = await async_client.post("/gameworlds/", json=gw_payload, headers=headers_gm1)
    world1_id = resp.json()["id"]
    resp = await async_client.post(
        "/tables/",
        json={"world_id": world1_id, "name": "TableA", "member_ids": []},
        headers=headers_gm1,
    )
    assert resp.status_code == 200, resp.text

    gw_payload = {"name": "WorldB", "system": "dnd", "description": "", "logo": ""}
    resp = await async_client.post("/gameworlds/", json=gw_payload, headers=headers_gm2)
    world2_id = resp.json()["id"]
    resp = await async_client.post(
        "/tables/",
        json={"world_id": world2_id, "name": "TableB", "member_ids": []},
        headers=headers_gm2,
    )
    assert resp.status_code == 200, resp.text

    resp = await async_client.get("/tables/", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    tables = resp.json()
    assert len(tables) == 2
