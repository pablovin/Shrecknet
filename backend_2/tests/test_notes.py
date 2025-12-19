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
    unshare_response = await client.request(
        "DELETE",
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


@pytest.mark.asyncio
async def test_shared_users_cannot_edit_note_only_respond(client):
    """Shared users cannot edit note content, only the owner can."""
    owner_payload = {
        "username": "shareable-owner",
        "password": "ShareableOwner123",
        "full_name": "Shareable Owner",
        "email": "shareable-owner@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    collaborator_payload = {
        "username": "shareable-collab",
        "password": "ShareableCollab123",
        "full_name": "Shareable Collaborator",
        "email": "shareable-collab@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }

    owner_register = await client.post("/users/", json=owner_payload)
    collaborator_register = await client.post("/users/", json=collaborator_payload)
    assert owner_register.status_code == 201, owner_register.text
    assert collaborator_register.status_code == 201, collaborator_register.text
    collaborator_id = collaborator_register.json()["id"]

    owner_token = await client.post(
        "/auth/token",
        data={
            "username": owner_payload["username"],
            "password": owner_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    collaborator_token = await client.post(
        "/auth/token",
        data={
            "username": collaborator_payload["username"],
            "password": collaborator_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert owner_token.status_code == 200, owner_token.text
    assert collaborator_token.status_code == 200, collaborator_token.text

    owner_headers = {"Authorization": f"Bearer {owner_token.json()['access_token']}"}
    collaborator_headers = {
        "Authorization": f"Bearer {collaborator_token.json()['access_token']}"
    }

    create_response = await client.post(
        "/notes/",
        json={
            "title": "Shared Note",
            "content": "<p>Initial</p>",
            "ontology_id": None,
            "share_user_ids": [collaborator_id],
        },
        headers=owner_headers,
    )
    assert create_response.status_code == 201, create_response.text
    note_id = create_response.json()["id"]

    # Collaborator should NOT be able to edit note content
    collaborator_update = await client.put(
        f"/notes/{note_id}",
        json={"content": "<p>Collaborator edit</p>"},
        headers=collaborator_headers,
    )
    assert collaborator_update.status_code == 403

    # Owner can still edit the note
    owner_update = await client.put(
        f"/notes/{note_id}",
        json={"content": "<p>Owner edit</p>", "share_user_ids": []},
        headers=owner_headers,
    )
    assert owner_update.status_code == 200, owner_update.text
    assert owner_update.json()["content"] == "<p>Owner edit</p>"
    assert owner_update.json()["shared_with"] == []


@pytest.mark.asyncio
async def test_note_responses_crud(client):
    """Test creating, reading, updating, and deleting responses to notes."""
    # Create owner and collaborator
    owner_payload = {
        "username": "response-owner",
        "password": "ResponseOwner123",
        "full_name": "Response Owner",
        "email": "response-owner@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    collaborator_payload = {
        "username": "response-collab",
        "password": "ResponseCollab123",
        "full_name": "Response Collaborator",
        "email": "response-collab@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }

    owner_register = await client.post("/users/", json=owner_payload)
    collaborator_register = await client.post("/users/", json=collaborator_payload)
    assert owner_register.status_code == 201
    assert collaborator_register.status_code == 201
    collaborator_id = collaborator_register.json()["id"]

    owner_token = await client.post(
        "/auth/token",
        data={
            "username": owner_payload["username"],
            "password": owner_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    collaborator_token = await client.post(
        "/auth/token",
        data={
            "username": collaborator_payload["username"],
            "password": collaborator_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert owner_token.status_code == 200
    assert collaborator_token.status_code == 200

    owner_headers = {"Authorization": f"Bearer {owner_token.json()['access_token']}"}
    collaborator_headers = {
        "Authorization": f"Bearer {collaborator_token.json()['access_token']}"
    }

    # Create a note and share it
    note_response = await client.post(
        "/notes/",
        json={
            "title": "Discussion Topic",
            "content": "<p>Let's discuss this</p>",
            "ontology_id": None,
            "share_user_ids": [collaborator_id],
        },
        headers=owner_headers,
    )
    assert note_response.status_code == 201
    note_id = note_response.json()["id"]

    # Collaborator creates a response
    response_create = await client.post(
        f"/notes/{note_id}/responses",
        json={"content": "<p>Great idea!</p>"},
        headers=collaborator_headers,
    )
    assert response_create.status_code == 201
    response_data = response_create.json()
    response_id = response_data["id"]
    assert response_data["content"] == "<p>Great idea!</p>"
    assert response_data["author"]["full_name"] == "Response Collaborator"
    assert response_data["note_id"] == note_id

    # List responses
    responses_list = await client.get(
        f"/notes/{note_id}/responses", headers=owner_headers
    )
    assert responses_list.status_code == 200
    responses = responses_list.json()
    assert len(responses) == 1
    assert responses[0]["id"] == response_id

    # Owner also responds
    owner_response = await client.post(
        f"/notes/{note_id}/responses",
        json={"content": "<p>Thanks for your input!</p>"},
        headers=owner_headers,
    )
    assert owner_response.status_code == 201

    # List should now have 2 responses
    responses_list = await client.get(
        f"/notes/{note_id}/responses", headers=collaborator_headers
    )
    assert responses_list.status_code == 200
    assert len(responses_list.json()) == 2

    # Collaborator updates their response
    update_response = await client.put(
        f"/notes/{note_id}/responses/{response_id}",
        json={"content": "<p>Even better idea!</p>"},
        headers=collaborator_headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["content"] == "<p>Even better idea!</p>"

    # Owner cannot update collaborator's response
    owner_update_attempt = await client.put(
        f"/notes/{note_id}/responses/{response_id}",
        json={"content": "<p>Trying to edit</p>"},
        headers=owner_headers,
    )
    assert owner_update_attempt.status_code == 403

    # Collaborator can delete their own response
    delete_response = await client.delete(
        f"/notes/{note_id}/responses/{response_id}", headers=collaborator_headers
    )
    assert delete_response.status_code == 204

    # Response should be gone
    responses_list = await client.get(
        f"/notes/{note_id}/responses", headers=owner_headers
    )
    assert responses_list.status_code == 200
    assert len(responses_list.json()) == 1


@pytest.mark.asyncio
async def test_response_access_control(client):
    """Test that only users with note access can respond."""
    # Create users
    owner_payload = {
        "username": "access-owner",
        "password": "AccessOwner123",
        "full_name": "Access Owner",
        "email": "access-owner@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    unauthorized_payload = {
        "username": "unauthorized-user",
        "password": "Unauthorized123",
        "full_name": "Unauthorized User",
        "email": "unauthorized@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }

    owner_register = await client.post("/users/", json=owner_payload)
    unauthorized_register = await client.post("/users/", json=unauthorized_payload)
    assert owner_register.status_code == 201
    assert unauthorized_register.status_code == 201

    owner_token = await client.post(
        "/auth/token",
        data={
            "username": owner_payload["username"],
            "password": owner_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    unauthorized_token = await client.post(
        "/auth/token",
        data={
            "username": unauthorized_payload["username"],
            "password": unauthorized_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    owner_headers = {"Authorization": f"Bearer {owner_token.json()['access_token']}"}
    unauthorized_headers = {
        "Authorization": f"Bearer {unauthorized_token.json()['access_token']}"
    }

    # Create a private note
    note_response = await client.post(
        "/notes/",
        json={
            "title": "Private Note",
            "content": "<p>Private content</p>",
            "ontology_id": None,
            "share_user_ids": [],
        },
        headers=owner_headers,
    )
    assert note_response.status_code == 201
    note_id = note_response.json()["id"]

    # Unauthorized user cannot respond
    unauthorized_response = await client.post(
        f"/notes/{note_id}/responses",
        json={"content": "<p>Trying to respond</p>"},
        headers=unauthorized_headers,
    )
    assert unauthorized_response.status_code == 403

    # Unauthorized user cannot list responses
    list_attempt = await client.get(
        f"/notes/{note_id}/responses", headers=unauthorized_headers
    )
    assert list_attempt.status_code == 403


@pytest.mark.asyncio
async def test_note_owner_can_delete_any_response(client):
    """Test that note owner can delete any response on their note."""
    owner_payload = {
        "username": "delete-owner",
        "password": "DeleteOwner123",
        "full_name": "Delete Owner",
        "email": "delete-owner@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    collaborator_payload = {
        "username": "delete-collab",
        "password": "DeleteCollab123",
        "full_name": "Delete Collaborator",
        "email": "delete-collab@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }

    owner_register = await client.post("/users/", json=owner_payload)
    collaborator_register = await client.post("/users/", json=collaborator_payload)
    assert owner_register.status_code == 201
    assert collaborator_register.status_code == 201
    collaborator_id = collaborator_register.json()["id"]

    owner_token = await client.post(
        "/auth/token",
        data={
            "username": owner_payload["username"],
            "password": owner_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    collaborator_token = await client.post(
        "/auth/token",
        data={
            "username": collaborator_payload["username"],
            "password": collaborator_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    owner_headers = {"Authorization": f"Bearer {owner_token.json()['access_token']}"}
    collaborator_headers = {
        "Authorization": f"Bearer {collaborator_token.json()['access_token']}"
    }

    # Create a note and share it
    note_response = await client.post(
        "/notes/",
        json={
            "title": "Moderated Discussion",
            "content": "<p>Discussion content</p>",
            "ontology_id": None,
            "share_user_ids": [collaborator_id],
        },
        headers=owner_headers,
    )
    assert note_response.status_code == 201
    note_id = note_response.json()["id"]

    # Collaborator creates a response
    response_create = await client.post(
        f"/notes/{note_id}/responses",
        json={"content": "<p>Collaborator response</p>"},
        headers=collaborator_headers,
    )
    assert response_create.status_code == 201
    response_id = response_create.json()["id"]

    # Note owner can delete the collaborator's response
    delete_response = await client.delete(
        f"/notes/{note_id}/responses/{response_id}", headers=owner_headers
    )
    assert delete_response.status_code == 204

    # Verify response is deleted
    responses_list = await client.get(
        f"/notes/{note_id}/responses", headers=owner_headers
    )
    assert responses_list.status_code == 200
    assert len(responses_list.json()) == 0
