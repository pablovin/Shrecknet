import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_gaming_session_notifications(
    async_client, create_user, login_and_get_token
):
    # Create users
    builder = await create_user(
        email="builder@example.com", password="pass", role="world builder"
    )
    player = await create_user(
        email="player@example.com", password="pass", role="player"
    )
    builder_token = await login_and_get_token(
        "builder@example.com", "pass", "world builder"
    )
    player_token = await login_and_get_token("player@example.com", "pass", "player")

    headers = {"Authorization": f"Bearer {builder_token}"}

    # Create game world and table with player
    gw_payload = {"name": "World", "system": "dnd", "description": "", "logo": ""}
    resp = await async_client.post("/gameworlds/", json=gw_payload, headers=headers)
    assert resp.status_code == 200
    world_id = resp.json()["id"]

    resp = await async_client.post(
        "/tables/",
        json={"world_id": world_id, "name": "Party", "member_ids": [player["id"]]},
        headers=headers,
    )
    assert resp.status_code == 200
    table_id = resp.json()["id"]

    # Player should receive notification about being added to table
    resp = await async_client.get(
        "/news/", headers={"Authorization": f"Bearer {player_token}"}
    )
    data = resp.json()
    assert any(n["title"] == "Added to Table" for n in data)

    # Create session for table
    sess_payload = {
        "name": "First Session",
        "scheduled_time": datetime.now(timezone.utc).isoformat(),
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
    assert resp.status_code == 200
    session_id = resp.json()["id"]

    resp = await async_client.get(
        "/news/", headers={"Authorization": f"Bearer {player_token}"}
    )
    data = resp.json()
    assert any(
        n["title"] == "Session Created" and "First Session" in n["description"]
        for n in data
    )

    resp = await async_client.get(
        "/news/", headers={"Authorization": f"Bearer {builder_token}"}
    )
    data = resp.json()
    assert any(
        n["title"] == "Session Created" and "First Session" in n["description"]
        for n in data
    )

    # Create poll for the session
    times = [datetime.now(timezone.utc).isoformat()]
    resp = await async_client.post(
        f"/tables/{table_id}/sessions/{session_id}/poll",
        json={"proposed_times": times},
        headers=headers,
    )
    assert resp.status_code == 200

    resp = await async_client.get(
        "/news/", headers={"Authorization": f"Bearer {player_token}"}
    )
    data = resp.json()
    assert any(n["title"] == "Poll Created" for n in data)
