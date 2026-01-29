from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.notification import NotificationType
from app.models.user import UserRole


def _dt(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


@pytest.mark.asyncio
async def test_game_session_poll_flow(client):
    # Create admin (auto elevated)
    admin_payload = {
        "username": "game-admin",
        "password": "AdminPass123",
        "full_name": "Game Admin",
        "email": "game-admin@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    admin_register = await client.post("/users/", json=admin_payload)
    assert admin_register.status_code == 201, admin_register.text
    admin_id = admin_register.json()["id"]

    admin_token_response = await client.post(
        "/auth/token",
        data={
            "username": admin_payload["username"],
            "password": admin_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert admin_token_response.status_code == 200, admin_token_response.text
    admin_token = admin_token_response.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Create player
    player_payload = {
        "username": "game-player",
        "password": "PlayerPass123",
        "full_name": "Game Player",
        "email": "game-player@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    player_register = await client.post("/users/", json=player_payload)
    assert player_register.status_code == 201, player_register.text
    player_id = player_register.json()["id"]

    player_token_response = await client.post(
        "/auth/token",
        data={
            "username": player_payload["username"],
            "password": player_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert player_token_response.status_code == 200, player_token_response.text
    player_token = player_token_response.json()["access_token"]
    player_headers = {"Authorization": f"Bearer {player_token}"}

    # Create ontology (required by game)
    ontology_response = await client.post(
        "/ontologies/",
        json={"name": "Campaign Ontology", "description": "For game testing"},
        headers=admin_headers,
    )
    assert ontology_response.status_code == 201, ontology_response.text
    ontology_id = ontology_response.json()["id"]

    # Create game with both members
    game_payload = {
        "name": "Epic Campaign",
        "ontology_id": ontology_id,
        "member_ids": [admin_id, player_id],
    }
    game_response = await client.post(
        "/games/", json=game_payload, headers=admin_headers
    )
    assert game_response.status_code == 201, game_response.text
    game = game_response.json()
    game_id = game["id"]
    assert len(game["members"]) == 2

    # Player can view games they belong to
    my_games = await client.get("/games/mine", headers=player_headers)
    assert my_games.status_code == 200
    assert any(item["id"] == game_id for item in my_games.json())

    # Admin creates a session (initially unscheduled)
    session_payload = {
        "title": "Session Zero",
        "summary": "Character creation",
        "location": "Workshop",
        "scheduled_date": None,
    }
    session_response = await client.post(
        f"/games/{game_id}/sessions",
        json=session_payload,
        headers=admin_headers,
    )
    assert session_response.status_code == 201, session_response.text
    session = session_response.json()
    session_id = session["id"]
    assert session["scheduled_date"] is None

    # Notifications should exist for player
    player_notifications = await client.get(
        f"/notifications/?user_id={player_id}", headers=admin_headers
    )
    assert player_notifications.status_code == 200
    assert any(
        note["notification_type"] == NotificationType.SESSION_UPDATES.value
        for note in player_notifications.json()
    )
    initial_notification_count = len(player_notifications.json())

    # Admin opens a scheduling poll
    poll_payload = {
        "options": [
            {"proposed_start": _dt(24)},
            {"proposed_start": _dt(48)},
        ]
    }
    poll_response = await client.post(
        f"/games/{game_id}/sessions/{session_id}/polls",
        json=poll_payload,
        headers=admin_headers,
    )
    assert poll_response.status_code == 201, poll_response.text
    poll = poll_response.json()
    poll_id = poll["id"]
    option_id = poll["options"][0]["id"]

    # Additional notification after poll creation
    later_notifications = await client.get(
        f"/notifications/?user_id={player_id}", headers=admin_headers
    )
    assert len(later_notifications.json()) >= initial_notification_count + 1

    # Player votes
    vote_response = await client.post(
        f"/games/{game_id}/sessions/{session_id}/polls/{poll_id}/vote",
        json={"option_id": option_id},
        headers=player_headers,
    )
    assert vote_response.status_code == 200, vote_response.text
    vote_poll = vote_response.json()
    assert any(
        option["id"] == option_id and option["vote_count"] == 1
        for option in vote_poll["options"]
    )

    # Admin finalizes poll selecting the voted option
    finalize_response = await client.post(
        f"/games/{game_id}/sessions/{session_id}/polls/{poll_id}/finalize",
        json={"option_id": option_id},
        headers=admin_headers,
    )
    assert finalize_response.status_code == 200, finalize_response.text
    finalized_session = finalize_response.json()
    assert finalized_session["scheduled_date"] == poll["options"][0]["proposed_start"]

    # Attendance should include the player marked as attending
    player_attendance = next(
        item for item in finalized_session["attendance"] if item["user_id"] == player_id
    )
    assert player_attendance["attending"] is True

    # Player toggles attendance off
    attendance_response = await client.post(
        f"/games/{game_id}/sessions/{session_id}/attendance",
        json={"attending": False},
        headers=player_headers,
    )
    assert attendance_response.status_code == 200, attendance_response.text
    updated_session = attendance_response.json()
    player_attendance_final = next(
        item for item in updated_session["attendance"] if item["user_id"] == player_id
    )
    assert player_attendance_final["attending"] is False


@pytest.mark.asyncio
async def test_admin_delete_vote(client):
    """Test that admin can delete a specific vote from a poll."""
    # Create admin
    admin_payload = {
        "username": "delete-admin",
        "password": "AdminPass123",
        "full_name": "Delete Admin",
        "email": "delete-admin@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    admin_register = await client.post("/users/", json=admin_payload)
    assert admin_register.status_code == 201
    admin_id = admin_register.json()["id"]

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

    # Create player
    player_payload = {
        "username": "delete-player",
        "password": "PlayerPass123",
        "full_name": "Delete Player",
        "email": "delete-player@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    player_register = await client.post("/users/", json=player_payload)
    assert player_register.status_code == 201
    player_id = player_register.json()["id"]

    player_token_response = await client.post(
        "/auth/token",
        data={
            "username": player_payload["username"],
            "password": player_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert player_token_response.status_code == 200
    player_token = player_token_response.json()["access_token"]
    player_headers = {"Authorization": f"Bearer {player_token}"}

    # Create ontology
    ontology_response = await client.post(
        "/ontologies/",
        json={"name": "Delete Test Ontology", "description": "For delete testing"},
        headers=admin_headers,
    )
    assert ontology_response.status_code == 201
    ontology_id = ontology_response.json()["id"]

    # Create game
    game_payload = {
        "name": "Delete Test Game",
        "ontology_id": ontology_id,
        "member_ids": [admin_id, player_id],
    }
    game_response = await client.post(
        "/games/", json=game_payload, headers=admin_headers
    )
    assert game_response.status_code == 201
    game_id = game_response.json()["id"]

    # Create session
    session_payload = {
        "title": "Delete Test Session",
        "summary": "Test",
        "location": "Online",
    }
    session_response = await client.post(
        f"/games/{game_id}/sessions", json=session_payload, headers=admin_headers
    )
    assert session_response.status_code == 201
    session_id = session_response.json()["id"]

    # Create poll
    poll_payload = {
        "options": [{"proposed_start": _dt(24)}, {"proposed_start": _dt(48)}]
    }
    poll_response = await client.post(
        f"/games/{game_id}/sessions/{session_id}/polls",
        json=poll_payload,
        headers=admin_headers,
    )
    assert poll_response.status_code == 201
    poll = poll_response.json()
    poll_id = poll["id"]
    option_id = poll["options"][0]["id"]

    # Player votes for first option
    vote_response = await client.post(
        f"/games/{game_id}/sessions/{session_id}/polls/{poll_id}/vote",
        json={"option_id": option_id},
        headers=player_headers,
    )
    assert vote_response.status_code == 200

    # Verify vote exists
    poll_details = await client.get(
        f"/games/{game_id}/sessions/{session_id}/polls/{poll_id}",
        headers=admin_headers,
    )
    assert poll_details.status_code == 200
    option_votes = [
        opt for opt in poll_details.json()["options"] if opt["id"] == option_id
    ][0]["votes"]
    assert player_id in [v["user_id"] for v in option_votes]

    # Admin deletes the vote
    delete_response = await client.delete(
        f"/games/{game_id}/sessions/{session_id}/polls/{poll_id}/votes/{player_id}/{option_id}",
        headers=admin_headers,
    )
    assert delete_response.status_code == 204

    # Verify vote is deleted
    poll_details_after = await client.get(
        f"/games/{game_id}/sessions/{session_id}/polls/{poll_id}",
        headers=admin_headers,
    )
    assert poll_details_after.status_code == 200
    option_votes_after = [
        opt for opt in poll_details_after.json()["options"] if opt["id"] == option_id
    ][0]["votes"]
    assert player_id not in [v["user_id"] for v in option_votes_after]


@pytest.mark.asyncio
async def test_admin_delete_poll(client):
    """Test that admin can delete a poll session."""
    # Create admin
    admin_payload = {
        "username": "poll-delete-admin",
        "password": "AdminPass123",
        "full_name": "Poll Delete Admin",
        "email": "poll-delete-admin@example.com",
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
        json={"name": "Poll Delete Ontology", "description": "For poll delete testing"},
        headers=admin_headers,
    )
    assert ontology_response.status_code == 201
    ontology_id = ontology_response.json()["id"]

    # Create game
    game_payload = {
        "name": "Poll Delete Game",
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
        "title": "Poll Delete Session",
        "summary": "Test",
        "location": "Online",
    }
    session_response = await client.post(
        f"/games/{game_id}/sessions", json=session_payload, headers=admin_headers
    )
    assert session_response.status_code == 201
    session_id = session_response.json()["id"]

    # Create poll
    poll_payload = {"options": [{"proposed_start": _dt(24)}]}
    poll_response = await client.post(
        f"/games/{game_id}/sessions/{session_id}/polls",
        json=poll_payload,
        headers=admin_headers,
    )
    assert poll_response.status_code == 201
    poll_id = poll_response.json()["id"]

    # Delete poll
    delete_response = await client.delete(
        f"/games/{game_id}/sessions/{session_id}/polls/{poll_id}",
        headers=admin_headers,
    )
    assert delete_response.status_code == 204

    # Verify poll is deleted (should return 404)
    poll_details = await client.get(
        f"/games/{game_id}/sessions/{session_id}/polls/{poll_id}",
        headers=admin_headers,
    )
    assert poll_details.status_code == 404


@pytest.mark.asyncio
async def test_admin_delete_session(client):
    """Test that admin can delete a session."""
    # Create admin
    admin_payload = {
        "username": "session-delete-admin",
        "password": "AdminPass123",
        "full_name": "Session Delete Admin",
        "email": "session-delete-admin@example.com",
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
            "name": "Session Delete Ontology",
            "description": "For session delete testing",
        },
        headers=admin_headers,
    )
    assert ontology_response.status_code == 201
    ontology_id = ontology_response.json()["id"]

    # Create game
    game_payload = {
        "name": "Session Delete Game",
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
        "title": "Delete Me Session",
        "summary": "Test",
        "location": "Online",
    }
    session_response = await client.post(
        f"/games/{game_id}/sessions", json=session_payload, headers=admin_headers
    )
    assert session_response.status_code == 201
    session_id = session_response.json()["id"]

    # Delete session
    delete_response = await client.delete(
        f"/games/{game_id}/sessions/{session_id}",
        headers=admin_headers,
    )
    assert delete_response.status_code == 204

    # Verify session is deleted (should return 404)
    session_details = await client.get(
        f"/games/{game_id}/sessions/{session_id}",
        headers=admin_headers,
    )
    assert session_details.status_code == 404
