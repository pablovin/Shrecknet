"""Test timezone enforcement for sessions, polls, and tables."""

import pytest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo


def assert_has_timezone_info(datetime_str: str) -> None:
    """Helper to assert that an ISO datetime string includes timezone information."""
    assert datetime_str is not None, "Datetime string should not be None"
    assert "+" in datetime_str or datetime_str.endswith(
        "Z"
    ), f"Datetime {datetime_str} should include timezone information"


@pytest.mark.anyio
async def test_session_requires_timezone_aware_datetime(async_client):
    """Test that creating a session requires timezone-aware datetime."""
    user = {
        "nickname": "tz_tester",
        "email": "tz_tester@example.com",
        "password": "secret123",
        "role": "world builder",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=user)
    assert resp.status_code == 200
    user_id = resp.json()["id"]

    login = await async_client.post(
        "/user/login",
        data={"username": user["email"], "password": user["password"]},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create world and table
    gw_payload = {"name": "TZWorld", "system": "dnd", "description": "", "logo": ""}
    resp = await async_client.post("/gameworlds/", json=gw_payload, headers=headers)
    world_id = resp.json()["id"]

    resp = await async_client.post(
        "/tables/",
        json={"world_id": world_id, "name": "TZTable", "member_ids": [user_id]},
        headers=headers,
    )
    table_id = resp.json()["id"]

    # Test 1: Session with timezone-aware datetime (should succeed)
    brussels_tz = ZoneInfo("Europe/Brussels")
    scheduled_time_aware = datetime.now(brussels_tz)

    sess_payload_aware = {
        "name": "Session with TZ",
        "scheduled_time": scheduled_time_aware.isoformat(),
        "summary": "",
        "location": "",
        "table_id": table_id,
        "timezone": "Europe/Brussels",
        "attendee_ids": [],
        "page_ids": [],
    }
    resp = await async_client.post(
        f"/tables/{table_id}/sessions", json=sess_payload_aware, headers=headers
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    session_data = resp.json()

    # Verify the response includes timezone info
    assert_has_timezone_info(session_data["scheduled_time"])
    assert_has_timezone_info(session_data["created_at"])

    # Test 2: Session with naive datetime (should fail)
    scheduled_time_naive = datetime.now()
    sess_payload_naive = {
        "name": "Session without TZ",
        "scheduled_time": scheduled_time_naive.isoformat(),  # No timezone info
        "summary": "",
        "location": "",
        "table_id": table_id,
        "timezone": "UTC",
        "attendee_ids": [],
        "page_ids": [],
    }
    resp = await async_client.post(
        f"/tables/{table_id}/sessions", json=sess_payload_naive, headers=headers
    )
    # Should fail validation
    assert (
        resp.status_code == 422
    ), f"Expected 422 for naive datetime, got {resp.status_code}"


@pytest.mark.anyio
async def test_poll_requires_timezone_aware_datetime(async_client):
    """Test that creating a poll requires timezone-aware datetimes."""
    user = {
        "nickname": "poll_tz_tester",
        "email": "poll_tz@example.com",
        "password": "secret123",
        "role": "world builder",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=user)
    assert resp.status_code == 200

    login = await async_client.post(
        "/user/login",
        data={"username": user["email"], "password": user["password"]},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create world and table
    gw_payload = {"name": "PollTZWorld", "system": "dnd", "description": "", "logo": ""}
    resp = await async_client.post("/gameworlds/", json=gw_payload, headers=headers)
    world_id = resp.json()["id"]

    resp = await async_client.post(
        "/tables/",
        json={"world_id": world_id, "name": "PollTZTable", "member_ids": []},
        headers=headers,
    )
    table_id = resp.json()["id"]

    # Create session
    sess_payload = {
        "name": "PollTestSession",
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

    # Test 1: Poll with timezone-aware datetimes (should succeed)
    brussels_tz = ZoneInfo("Europe/Brussels")
    times_aware = [
        (datetime.now(brussels_tz) + timedelta(days=i)).isoformat() for i in range(3)
    ]

    resp = await async_client.post(
        f"/tables/{table_id}/sessions/{session_id}/poll",
        json={"proposed_times": times_aware, "timezone": "Europe/Brussels"},
        headers=headers,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    poll_data = resp.json()

    # Verify all options have timezone info
    for option in poll_data["options"]:
        assert_has_timezone_info(option["proposed_time"])

    # Verify created_at has timezone info
    assert_has_timezone_info(poll_data["created_at"])

    # Create another session for naive datetime test
    resp = await async_client.post(
        f"/tables/{table_id}/sessions", json=sess_payload, headers=headers
    )
    session_id2 = resp.json()["id"]

    # Test 2: Poll with naive datetimes (should fail)
    times_naive = [(datetime.now() + timedelta(days=i)).isoformat() for i in range(3)]

    resp = await async_client.post(
        f"/tables/{table_id}/sessions/{session_id2}/poll",
        json={"proposed_times": times_naive, "timezone": "UTC"},
        headers=headers,
    )
    # Should fail validation
    assert (
        resp.status_code == 422
    ), f"Expected 422 for naive datetimes, got {resp.status_code}"


@pytest.mark.anyio
async def test_table_list_returns_timezone_aware_datetimes(async_client):
    """Test that table listings return timezone-aware session times."""
    user = {
        "nickname": "table_tz_tester",
        "email": "table_tz@example.com",
        "password": "secret123",
        "role": "world builder",
        "image_url": "no image",
    }
    resp = await async_client.post("/user/", json=user)
    assert resp.status_code == 200

    login = await async_client.post(
        "/user/login",
        data={"username": user["email"], "password": user["password"]},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create world and table
    gw_payload = {
        "name": "TableListTZWorld",
        "system": "dnd",
        "description": "",
        "logo": "",
    }
    resp = await async_client.post("/gameworlds/", json=gw_payload, headers=headers)
    world_id = resp.json()["id"]

    resp = await async_client.post(
        "/tables/",
        json={"world_id": world_id, "name": "TableListTZ", "member_ids": []},
        headers=headers,
    )
    table_id = resp.json()["id"]
    table_created_at = resp.json()["created_at"]

    # Verify table created_at has timezone info
    assert_has_timezone_info(table_created_at)

    # Create a session with timezone-aware datetime
    brussels_tz = ZoneInfo("Europe/Brussels")
    scheduled_time = datetime.now(brussels_tz) + timedelta(days=1)

    sess_payload = {
        "name": "Future Session",
        "scheduled_time": scheduled_time.isoformat(),
        "summary": "",
        "location": "",
        "table_id": table_id,
        "timezone": "Europe/Brussels",
        "attendee_ids": [],
        "page_ids": [],
    }
    resp = await async_client.post(
        f"/tables/{table_id}/sessions", json=sess_payload, headers=headers
    )
    assert resp.status_code == 200

    # Get table list
    resp = await async_client.get("/tables/", headers=headers)
    assert resp.status_code == 200
    tables = resp.json()

    # Find our table
    our_table = next(t for t in tables if t["id"] == table_id)

    # Verify next_session has timezone info
    assert_has_timezone_info(our_table["next_session"])
