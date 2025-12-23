"""Tests for timezone validation in sessions, polls, and tables."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.game import (
    GameSessionCreate,
    GameSessionUpdate,
    GameSessionPollOptionCreate,
    GameSessionRead,
    GameSessionPollRead,
    GameSessionPollOptionRead,
    GameRead,
)


def test_game_session_create_requires_timezone():
    """Test that GameSessionCreate requires timezone-aware datetime."""
    # Timezone-aware datetime should work
    aware_dt = datetime.now(timezone.utc)
    session = GameSessionCreate(
        title="Test Session",
        scheduled_date=aware_dt,
    )
    assert session.scheduled_date == aware_dt
    assert session.scheduled_date.tzinfo is not None

    # None should work
    session_none = GameSessionCreate(title="Test Session", scheduled_date=None)
    assert session_none.scheduled_date is None

    # Naive datetime should raise ValidationError
    naive_dt = datetime.now()
    with pytest.raises(ValidationError) as exc_info:
        GameSessionCreate(
            title="Test Session",
            scheduled_date=naive_dt,
        )
    assert "scheduled_date must include timezone information" in str(exc_info.value)


def test_game_session_update_requires_timezone():
    """Test that GameSessionUpdate requires timezone-aware datetime."""
    # Timezone-aware datetime should work
    aware_dt = datetime.now(timezone.utc)
    update = GameSessionUpdate(scheduled_date=aware_dt)
    assert update.scheduled_date == aware_dt
    assert update.scheduled_date.tzinfo is not None

    # None should work
    update_none = GameSessionUpdate(scheduled_date=None)
    assert update_none.scheduled_date is None

    # Naive datetime should raise ValidationError
    naive_dt = datetime.now()
    with pytest.raises(ValidationError) as exc_info:
        GameSessionUpdate(scheduled_date=naive_dt)
    assert "scheduled_date must include timezone information" in str(exc_info.value)


def test_poll_option_create_requires_timezone():
    """Test that GameSessionPollOptionCreate requires timezone-aware datetime."""
    # Timezone-aware datetime should work
    aware_dt = datetime.now(timezone.utc) + timedelta(days=1)
    option = GameSessionPollOptionCreate(proposed_start=aware_dt)
    assert option.proposed_start == aware_dt
    assert option.proposed_start.tzinfo is not None

    # Naive datetime should raise ValidationError
    naive_dt = datetime.now() + timedelta(days=1)
    with pytest.raises(ValidationError) as exc_info:
        GameSessionPollOptionCreate(proposed_start=naive_dt)
    assert "proposed_start must include timezone information" in str(exc_info.value)


def test_game_read_converts_naive_to_utc():
    """Test that GameRead converts naive datetimes to UTC."""
    # Naive datetime should be converted to UTC
    naive_dt = datetime(2024, 1, 1, 12, 0, 0)
    game = GameRead(
        id=1,
        name="Test Game",
        ontology_id=1,
        created_at=naive_dt,
        updated_at=naive_dt,
        members=[],
    )
    assert game.created_at.tzinfo == timezone.utc
    assert game.updated_at.tzinfo == timezone.utc
    assert game.created_at.replace(tzinfo=None) == naive_dt

    # Timezone-aware datetime should be preserved
    aware_dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    game_aware = GameRead(
        id=2,
        name="Test Game 2",
        ontology_id=1,
        created_at=aware_dt,
        updated_at=aware_dt,
        members=[],
    )
    assert game_aware.created_at == aware_dt
    assert game_aware.updated_at == aware_dt


def test_game_session_read_converts_naive_to_utc():
    """Test that GameSessionRead converts naive datetimes to UTC."""
    naive_dt = datetime(2024, 1, 1, 12, 0, 0)
    session = GameSessionRead(
        id=1,
        game_id=1,
        title="Test Session",
        scheduled_date=naive_dt,
        location="Online",
        summary="Test",
        created_at=naive_dt,
        updated_at=naive_dt,
        attendance=[],
        polls=[],
    )
    assert session.created_at.tzinfo == timezone.utc
    assert session.updated_at.tzinfo == timezone.utc
    assert session.scheduled_date.tzinfo == timezone.utc

    # None should remain None
    session_none = GameSessionRead(
        id=2,
        game_id=1,
        title="Test Session 2",
        scheduled_date=None,
        location="Online",
        summary="Test",
        created_at=naive_dt,
        updated_at=naive_dt,
        attendance=[],
        polls=[],
    )
    assert session_none.scheduled_date is None


def test_poll_read_converts_naive_to_utc():
    """Test that GameSessionPollRead converts naive datetimes to UTC."""
    naive_dt = datetime(2024, 1, 1, 12, 0, 0)
    poll = GameSessionPollRead(
        id=1,
        created_at=naive_dt,
        is_finalized=False,
        finalized_option_id=None,
        options=[],
    )
    assert poll.created_at.tzinfo == timezone.utc


def test_poll_option_read_converts_naive_to_utc():
    """Test that GameSessionPollOptionRead converts naive datetimes to UTC."""
    naive_dt = datetime(2024, 1, 1, 12, 0, 0)
    option = GameSessionPollOptionRead(
        id=1,
        proposed_start=naive_dt,
        vote_count=0,
    )
    assert option.proposed_start.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_session_with_timezone_aware_date_e2e(client):
    """End-to-end test creating a session with timezone-aware datetime."""
    from app.models.user import UserRole

    # Create admin
    admin_payload = {
        "username": "tz-admin",
        "password": "AdminPass123",
        "full_name": "TZ Admin",
        "email": "tz-admin@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    admin_register = await client.post("/users/", json=admin_payload)
    assert admin_register.status_code == 201
    admin_token_response = await client.post(
        "/auth/token",
        data={
            "username": admin_payload["username"],
            "password": admin_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert admin_token_response.status_code == 200
    admin_token = admin_token_response.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Create ontology
    ontology_response = await client.post(
        "/ontologies/",
        json={"name": "TZ Ontology", "description": "For timezone testing"},
        headers=admin_headers,
    )
    assert ontology_response.status_code == 201
    ontology_id = ontology_response.json()["id"]

    # Create game
    game_payload = {
        "name": "TZ Game",
        "ontology_id": ontology_id,
        "member_ids": [],
    }
    game_response = await client.post(
        "/games/", json=game_payload, headers=admin_headers
    )
    assert game_response.status_code == 201
    game_id = game_response.json()["id"]

    # Create session with timezone-aware datetime
    scheduled_date = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    session_payload = {
        "title": "TZ Session",
        "scheduled_date": scheduled_date,
        "location": "Online",
        "summary": "Testing timezone",
    }
    session_response = await client.post(
        f"/games/{game_id}/sessions",
        json=session_payload,
        headers=admin_headers,
    )
    assert session_response.status_code == 201
    session = session_response.json()
    assert session["scheduled_date"] is not None
    # Verify the datetime is timezone-aware by checking it ends with timezone info
    assert "+" in session["scheduled_date"] or session["scheduled_date"].endswith("Z")


@pytest.mark.asyncio
async def test_session_with_naive_datetime_fails(client):
    """Test that creating a session with naive datetime fails."""
    from app.models.user import UserRole

    # Create admin
    admin_payload = {
        "username": "naive-admin",
        "password": "AdminPass123",
        "full_name": "Naive Admin",
        "email": "naive-admin@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    admin_register = await client.post("/users/", json=admin_payload)
    assert admin_register.status_code == 201
    admin_token_response = await client.post(
        "/auth/token",
        data={
            "username": admin_payload["username"],
            "password": admin_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert admin_token_response.status_code == 200
    admin_token = admin_token_response.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Create ontology
    ontology_response = await client.post(
        "/ontologies/",
        json={"name": "Naive Ontology", "description": "For naive testing"},
        headers=admin_headers,
    )
    assert ontology_response.status_code == 201
    ontology_id = ontology_response.json()["id"]

    # Create game
    game_payload = {
        "name": "Naive Game",
        "ontology_id": ontology_id,
        "member_ids": [],
    }
    game_response = await client.post(
        "/games/", json=game_payload, headers=admin_headers
    )
    assert game_response.status_code == 201
    game_id = game_response.json()["id"]

    # Try to create session with naive datetime (no timezone)
    # Use relative date to avoid outdated hardcoded dates
    naive_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    session_payload = {
        "title": "Naive Session",
        "scheduled_date": naive_date,
        "location": "Online",
        "summary": "Should fail",
    }
    session_response = await client.post(
        f"/games/{game_id}/sessions",
        json=session_payload,
        headers=admin_headers,
    )
    # Should fail validation
    assert session_response.status_code == 422
    assert "scheduled_date must include timezone information" in session_response.text


@pytest.mark.asyncio
async def test_poll_with_timezone_aware_date_e2e(client):
    """End-to-end test creating a poll with timezone-aware datetime."""
    from app.models.user import UserRole

    # Create admin
    admin_payload = {
        "username": "poll-tz-admin",
        "password": "AdminPass123",
        "full_name": "Poll TZ Admin",
        "email": "poll-tz-admin@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    admin_register = await client.post("/users/", json=admin_payload)
    assert admin_register.status_code == 201
    admin_token_response = await client.post(
        "/auth/token",
        data={
            "username": admin_payload["username"],
            "password": admin_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert admin_token_response.status_code == 200
    admin_token = admin_token_response.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Create ontology
    ontology_response = await client.post(
        "/ontologies/",
        json={"name": "Poll TZ Ontology", "description": "For poll timezone testing"},
        headers=admin_headers,
    )
    assert ontology_response.status_code == 201
    ontology_id = ontology_response.json()["id"]

    # Create game
    game_payload = {
        "name": "Poll TZ Game",
        "ontology_id": ontology_id,
        "member_ids": [],
    }
    game_response = await client.post(
        "/games/", json=game_payload, headers=admin_headers
    )
    assert game_response.status_code == 201
    game_id = game_response.json()["id"]

    # Create session
    session_payload = {
        "title": "Poll TZ Session",
        "location": "Online",
        "summary": "Testing poll timezone",
    }
    session_response = await client.post(
        f"/games/{game_id}/sessions",
        json=session_payload,
        headers=admin_headers,
    )
    assert session_response.status_code == 201
    session_id = session_response.json()["id"]

    # Create poll with timezone-aware datetimes
    option1 = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    option2 = (datetime.now(timezone.utc) + timedelta(days=8)).isoformat()
    poll_payload = {
        "options": [{"proposed_start": option1}, {"proposed_start": option2}]
    }
    poll_response = await client.post(
        f"/games/{game_id}/sessions/{session_id}/polls",
        json=poll_payload,
        headers=admin_headers,
    )
    assert poll_response.status_code == 201
    poll = poll_response.json()
    assert len(poll["options"]) == 2
    # Verify the datetimes are timezone-aware
    for option in poll["options"]:
        assert "+" in option["proposed_start"] or option["proposed_start"].endswith("Z")


@pytest.mark.asyncio
async def test_poll_with_naive_datetime_fails(client):
    """Test that creating a poll with naive datetime fails."""
    from app.models.user import UserRole

    # Create admin
    admin_payload = {
        "username": "poll-naive-admin",
        "password": "AdminPass123",
        "full_name": "Poll Naive Admin",
        "email": "poll-naive-admin@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    admin_register = await client.post("/users/", json=admin_payload)
    assert admin_register.status_code == 201
    admin_token_response = await client.post(
        "/auth/token",
        data={
            "username": admin_payload["username"],
            "password": admin_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert admin_token_response.status_code == 200
    admin_token = admin_token_response.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Create ontology
    ontology_response = await client.post(
        "/ontologies/",
        json={
            "name": "Poll Naive Ontology",
            "description": "For poll naive testing",
        },
        headers=admin_headers,
    )
    assert ontology_response.status_code == 201
    ontology_id = ontology_response.json()["id"]

    # Create game
    game_payload = {
        "name": "Poll Naive Game",
        "ontology_id": ontology_id,
        "member_ids": [],
    }
    game_response = await client.post(
        "/games/", json=game_payload, headers=admin_headers
    )
    assert game_response.status_code == 201
    game_id = game_response.json()["id"]

    # Create session
    session_payload = {
        "title": "Poll Naive Session",
        "location": "Online",
        "summary": "Testing poll naive",
    }
    session_response = await client.post(
        f"/games/{game_id}/sessions",
        json=session_payload,
        headers=admin_headers,
    )
    assert session_response.status_code == 201
    session_id = session_response.json()["id"]

    # Try to create poll with naive datetime (no timezone)
    # Use relative dates to avoid outdated hardcoded dates
    base_date = datetime.now() + timedelta(days=30)
    naive_date1 = base_date.strftime("%Y-%m-%dT%H:%M:%S")
    naive_date2 = (base_date + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    poll_payload = {
        "options": [
            {"proposed_start": naive_date1},
            {"proposed_start": naive_date2},
        ]
    }
    poll_response = await client.post(
        f"/games/{game_id}/sessions/{session_id}/polls",
        json=poll_payload,
        headers=admin_headers,
    )
    # Should fail validation
    assert poll_response.status_code == 422
    assert "proposed_start must include timezone information" in poll_response.text
