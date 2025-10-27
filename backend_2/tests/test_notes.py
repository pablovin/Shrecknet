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
        json={"content": "<p>Updated prep</p>", "share_user_ids": [],},
        headers=owner_headers,
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["shared_with"] == []

    get_response = await client.get(f"/notes/{note_id}", headers=collaborator_headers)
    assert get_response.status_code == 403

    delete_response = await client.delete(f"/notes/{note_id}", headers=owner_headers)
    assert delete_response.status_code == 204
