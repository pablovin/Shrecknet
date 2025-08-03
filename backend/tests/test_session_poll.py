import pytest
from datetime import datetime, timezone, timedelta


@pytest.mark.anyio
async def test_session_poll_sets_session_date(async_client):
    user = {
        "nickname": "gm3",
        "email": "gm3@example.com",
        "password": "secret123",
        "role": "world builder",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=user)
    assert resp.status_code == 200, resp.text
    login = await async_client.post(
        "/user/login",
        data={"username": user["email"], "password": user["password"]},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    gw_payload = {"name": "World", "system": "dnd", "description": "", "logo": ""}
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
        "name": "Session",
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
    session_id = resp.json()["id"]

    times = [
        datetime.now(timezone.utc).isoformat(),
        (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    ]
    resp = await async_client.post(
        f"/tables/{table_id}/sessions/{session_id}/poll",
        json={"proposed_times": times},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    poll = resp.json()
    option_id = poll["options"][0]["id"]

    resp = await async_client.post(
        f"/tables/{table_id}/sessions/{session_id}/poll/finalize",
        json={"option_id": option_id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await async_client.get(f"/tables/{table_id}/sessions", headers=headers)
    assert resp.status_code == 200, resp.text
    sessions = resp.json()
    session_info = next(s for s in sessions if s["id"] == session_id)
    assert session_info["scheduled_time"] == times[0]


@pytest.mark.anyio
async def test_session_poll_multi_vote(async_client):
    user = {
        "nickname": "player",
        "email": "player@example.com",
        "password": "secret123",
        "role": "world builder",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=user)
    player_id = resp.json()["id"]
    assert resp.status_code == 200, resp.text
    login = await async_client.post(
        "/user/login",
        data={"username": user["email"], "password": user["password"]},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    gw_payload = {"name": "World", "system": "dnd", "description": "", "logo": ""}
    resp = await async_client.post("/gameworlds/", json=gw_payload, headers=headers)
    world_id = resp.json()["id"]

    resp = await async_client.post(
        "/tables/",
        json={"world_id": world_id, "name": "Party", "member_ids": []},
        headers=headers,
    )
    table_id = resp.json()["id"]

    sess_payload = {
        "name": "Session",
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
    session_id = resp.json()["id"]

    times = [
        datetime.now(timezone.utc).isoformat(),
        (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    ]
    resp = await async_client.post(
        f"/tables/{table_id}/sessions/{session_id}/poll",
        json={"proposed_times": times},
        headers=headers,
    )
    poll = resp.json()
    opt_ids = [opt["id"] for opt in poll["options"]]

    resp = await async_client.post(
        f"/tables/{table_id}/sessions/{session_id}/poll/vote",
        json={"option_ids": opt_ids},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    poll_data = resp.json()
    for option in poll_data["options"]:
        assert option["votes"] == [player_id]

    # Change vote to only second option
    resp = await async_client.post(
        f"/tables/{table_id}/sessions/{session_id}/poll/vote",
        json={"option_ids": [opt_ids[1]]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    poll_data = resp.json()
    assert poll_data["options"][0]["votes"] == []
    assert poll_data["options"][1]["votes"] == [player_id]
