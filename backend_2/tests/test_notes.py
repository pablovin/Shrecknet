from __future__ import annotations

import pytest

from app.models.notification import NotificationType
from app.models.user import UserRole


@pytest.mark.asyncio
async def test_notes_crud_and_sharing(client):
    owner_payload = {
        "username": "note-owner",
        "password": "NoteOwner123",
        "full_name": "Note Owner",
        "email": "note-owner@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    owner_register = await client.post("/users/", json=owner_payload)
    assert owner_register.status_code == 201, owner_register.text
    owner_id = owner_register.json()["id"]

    owner_token = await client.post(
        "/auth/token",
        data={
            "username": owner_payload["username"],
            "password": owner_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert owner_token.status_code == 200, owner_token.text
    owner_headers = {"Authorization": f"Bearer {owner_token.json()['access_token']}"}

    collaborator_payload = {
        "username": "note-collab",
        "password": "NoteCollab123",
        "full_name": "Note Collaborator",
        "email": "note-collab@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    collaborator_register = await client.post("/users/", json=collaborator_payload)
    assert collaborator_register.status_code == 201, collaborator_register.text
    collaborator_id = collaborator_register.json()["id"]

    collaborator_token = await client.post(
        "/auth/token",
        data={
            "username": collaborator_payload["username"],
            "password": collaborator_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert collaborator_token.status_code == 200, collaborator_token.text
    collaborator_headers = {
        "Authorization": f"Bearer {collaborator_token.json()['access_token']}"
    }

    ontology_response = await client.post(
        "/ontologies/",
        json={"name": "Note Ontology", "description": "Notes"},
        headers=owner_headers,
    )
    assert ontology_response.status_code == 201, ontology_response.text
    ontology_id = ontology_response.json()["id"]

    create_response = await client.post(
        "/notes/",
        json={
            "title": "Preparation",
            "content": "<p>Session prep</p>",
            "ontology_id": ontology_id,
            "share_user_ids": [collaborator_id],
        },
        headers=owner_headers,
    )
    assert create_response.status_code == 201, create_response.text
    note = create_response.json()
    note_id = note["id"]
    assert note["shared_with"] == [collaborator_id]

    shared_list = await client.get("/notes/shared", headers=collaborator_headers)
    assert shared_list.status_code == 200
    assert any(entry["id"] == note_id for entry in shared_list.json())

    notes_notifications = await client.get(
        f"/notifications/?user_id={collaborator_id}", headers=owner_headers
    )
    assert notes_notifications.status_code == 200
    assert any(
        item["notification_type"] == NotificationType.NOTE_UPDATES.value
        for item in notes_notifications.json()
    )

    update_response = await client.put(
        f"/notes/{note_id}",
        json={
            "content": "<p>Updated prep</p>",
            "share_user_ids": [],
        },
        headers=owner_headers,
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["shared_with"] == []

    get_response = await client.get(f"/notes/{note_id}", headers=collaborator_headers)
    assert get_response.status_code == 403

    delete_response = await client.delete(f"/notes/{note_id}", headers=owner_headers)
    assert delete_response.status_code == 204


@pytest.mark.asyncio
async def test_share_unshare_note_endpoints(client):
    """Test the dedicated share/unshare endpoints for managing note access."""
    # Create owner user
    owner_payload = {
        "username": "share-owner",
        "password": "ShareOwner123",
        "full_name": "Share Owner",
        "email": "share-owner@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    owner_register = await client.post("/users/", json=owner_payload)
    assert owner_register.status_code == 201, owner_register.text
    owner_id = owner_register.json()["id"]

    owner_token = await client.post(
        "/auth/token",
        data={
            "username": owner_payload["username"],
            "password": owner_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert owner_token.status_code == 200, owner_token.text
    owner_headers = {"Authorization": f"Bearer {owner_token.json()['access_token']}"}

    # Create first collaborator
    user1_payload = {
        "username": "user1",
        "password": "User1Pass123",
        "full_name": "User One",
        "email": "user1@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    user1_register = await client.post("/users/", json=user1_payload)
    assert user1_register.status_code == 201, user1_register.text
    user1_id = user1_register.json()["id"]

    user1_token = await client.post(
        "/auth/token",
        data={
            "username": user1_payload["username"],
            "password": user1_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert user1_token.status_code == 200, user1_token.text
    user1_headers = {"Authorization": f"Bearer {user1_token.json()['access_token']}"}

    # Create second collaborator
    user2_payload = {
        "username": "user2",
        "password": "User2Pass123",
        "full_name": "User Two",
        "email": "user2@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    user2_register = await client.post("/users/", json=user2_payload)
    assert user2_register.status_code == 201, user2_register.text
    user2_id = user2_register.json()["id"]

    user2_token = await client.post(
        "/auth/token",
        data={
            "username": user2_payload["username"],
            "password": user2_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert user2_token.status_code == 200, user2_token.text
    user2_headers = {"Authorization": f"Bearer {user2_token.json()['access_token']}"}

    # Create a note without sharing
    create_response = await client.post(
        "/notes/",
        json={
            "title": "Private Note",
            "content": "<p>This is private</p>",
            "ontology_id": None,
            "share_user_ids": [],
        },
        headers=owner_headers,
    )
    assert create_response.status_code == 201, create_response.text
    note = create_response.json()
    note_id = note["id"]
    assert note["shared_with"] == []

    # User1 should not be able to access the note
    get_response = await client.get(f"/notes/{note_id}", headers=user1_headers)
    assert get_response.status_code == 403

    # Share the note with user1 using the dedicated endpoint
    share_response = await client.post(
        f"/notes/{note_id}/share",
        json={"user_ids": [user1_id]},
        headers=owner_headers,
    )
    assert share_response.status_code == 200, share_response.text
    assert user1_id in share_response.json()["shared_with"]

    # User1 should now be able to access the note
    get_response = await client.get(f"/notes/{note_id}", headers=user1_headers)
    assert get_response.status_code == 200

    # User1 should see it in their shared notes
    shared_list = await client.get("/notes/shared", headers=user1_headers)
    assert shared_list.status_code == 200
    assert any(entry["id"] == note_id for entry in shared_list.json())

    # Share with user2 as well
    share_response = await client.post(
        f"/notes/{note_id}/share",
        json={"user_ids": [user2_id]},
        headers=owner_headers,
    )
    assert share_response.status_code == 200, share_response.text
    shared_with = share_response.json()["shared_with"]
    assert user1_id in shared_with
    assert user2_id in shared_with

    # User2 should be able to access the note
    get_response = await client.get(f"/notes/{note_id}", headers=user2_headers)
    assert get_response.status_code == 200

    # Unshare from user1 using the dedicated endpoint
    unshare_response = await client.delete(
        f"/notes/{note_id}/share",
        json={"user_ids": [user1_id]},
        headers=owner_headers,
    )
    assert unshare_response.status_code == 200, unshare_response.text
    shared_with = unshare_response.json()["shared_with"]
    assert user1_id not in shared_with
    assert user2_id in shared_with

    # User1 should no longer be able to access the note
    get_response = await client.get(f"/notes/{note_id}", headers=user1_headers)
    assert get_response.status_code == 403

    # User2 should still be able to access the note
    get_response = await client.get(f"/notes/{note_id}", headers=user2_headers)
    assert get_response.status_code == 200

    # Only owner can share/unshare - user2 should not be able to share
    share_response = await client.post(
        f"/notes/{note_id}/share",
        json={"user_ids": [user1_id]},
        headers=user2_headers,
    )
    assert share_response.status_code == 403

    # Sharing with non-existent user should return 400
    share_response = await client.post(
        f"/notes/{note_id}/share",
        json={"user_ids": [99999]},
        headers=owner_headers,
    )
    assert share_response.status_code == 400
