from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import get_settings
from app.models.ontology import AuthorType, Cardinality, PropertyDataType
from app.models.user import UserRole


def _create_image() -> BytesIO:
    image = Image.new("RGB", (200, 200), color="green")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


@pytest.mark.asyncio
async def test_upload_model_image_and_validation(client):
    admin_payload = {
        "username": "media-admin",
        "password": "MediaAdmin123",
        "full_name": "Media Admin",
        "email": "media-admin@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    admin_register = await client.post("/users/", json=admin_payload)
    assert admin_register.status_code == 201, admin_register.text

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

    user_payload = {
        "username": "media-target",
        "password": "MediaTarget123",
        "full_name": "Media Target",
        "email": "media-target@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    user_response = await client.post("/users/", json=user_payload)
    assert user_response.status_code == 201, user_response.text
    user_id = str(user_response.json()["id"])

    buffer = _create_image()
    upload_response = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("image.png", buffer, "image/png")},
        data={"content_type": "user", "content_id": user_id, "is_main": "true"},
    )
    assert upload_response.status_code == 201, upload_response.text
    url = upload_response.json()["url"]
    settings = get_settings()
    base_url = (
        settings.media_public_url.rstrip("/")
        if settings.media_public_url
        else settings.media_base_url.rstrip("/")
    )
    assert url == f"{base_url}/user/{user_id}/file.png"

    media_root = Path(settings.media_root)
    created_paths: list[Path] = []
    image_path = media_root / "user" / user_id / "file.png"
    assert image_path.exists()
    created_paths.append(image_path)

    # Prepare ontology resources for entity/property/relationship uploads
    ontology_response = await client.post(
        "/ontologies/",
        json={"name": "Media Ontology", "description": "Media assets"},
        headers=admin_headers,
    )
    assert ontology_response.status_code == 201, ontology_response.text
    ontology_id = ontology_response.json()["id"]

    core_entity_payload = {
        "name": "Entity Hero",
        "description": "Hero entity",
        "keywords": ["hero"],
        "author_type": AuthorType.HUMAN.value,
        "user_id": "user-entity",
    }
    entity_response = await client.post(
        f"/ontologies/{ontology_id}/entities",
        json=core_entity_payload,
        headers=admin_headers,
    )
    assert entity_response.status_code == 201, entity_response.text
    entity_id = str(entity_response.json()["id"])

    supporting_entity_payload = {
        "name": "Entity Mentor",
        "description": "Mentor entity",
        "author_type": AuthorType.AGENT.value,
        "agent_id": "agent-mentor",
    }
    supporting_entity_response = await client.post(
        f"/ontologies/{ontology_id}/entities",
        json=supporting_entity_payload,
        headers=admin_headers,
    )
    assert (
        supporting_entity_response.status_code == 201
    ), supporting_entity_response.text
    supporting_entity_id = str(supporting_entity_response.json()["id"])

    property_payload = {
        "name": "Courage",
        "cardinality": Cardinality.ONE.value,
        "data_type": PropertyDataType.NUMBER.value,
        "author_type": AuthorType.AGENT.value,
        "agent_id": "agent-property",
    }
    property_response = await client.post(
        f"/ontologies/{ontology_id}/entities/{entity_id}/properties",
        json=property_payload,
        headers=admin_headers,
    )
    assert property_response.status_code == 201, property_response.text
    property_id = str(property_response.json()["id"])

    relationship_payload = {
        "name": "Mentorship",
        "bi_directional": False,
        "destiny_entity_id": int(supporting_entity_id),
        "author_type": AuthorType.AGENT.value,
        "agent_id": "agent-relationship",
    }
    relationship_response = await client.post(
        f"/ontologies/{ontology_id}/entities/{entity_id}/relationships",
        json=relationship_payload,
        headers=admin_headers,
    )
    assert relationship_response.status_code == 201, relationship_response.text
    relationship_id = str(relationship_response.json()["id"])

    # Entity upload
    buffer = _create_image()
    entity_upload = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("entity.png", buffer, "image/png")},
        data={"content_type": "entity", "content_id": entity_id, "is_main": "true"},
    )
    assert entity_upload.status_code == 201, entity_upload.text
    assert entity_upload.json()["url"] == f"{base_url}/entity/{entity_id}/file.png"
    entity_path = media_root / "entity" / entity_id / "file.png"
    assert entity_path.exists()
    created_paths.append(entity_path)

    # Property upload
    buffer = _create_image()
    property_upload = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("property.png", buffer, "image/png")},
        data={"content_type": "property", "content_id": property_id, "is_main": "true"},
    )
    assert property_upload.status_code == 201, property_upload.text
    assert (
        property_upload.json()["url"] == f"{base_url}/property/{property_id}/file.png"
    )
    property_path = media_root / "property" / property_id / "file.png"
    assert property_path.exists()
    created_paths.append(property_path)

    # Relationship upload
    buffer = _create_image()
    relationship_upload = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("relationship.png", buffer, "image/png")},
        data={
            "content_type": "relationship",
            "content_id": relationship_id,
            "is_main": "true",
        },
    )
    assert relationship_upload.status_code == 201, relationship_upload.text
    assert (
        relationship_upload.json()["url"]
        == f"{base_url}/relationship/{relationship_id}/file.png"
    )
    relationship_path = media_root / "relationship" / relationship_id / "file.png"
    assert relationship_path.exists()
    created_paths.append(relationship_path)

    # Invalid content_type test
    buffer = _create_image()
    unsupported = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("image.png", buffer, "image/png")},
        data={"content_type": "unknown/../etc", "content_id": "1", "is_main": "false"},
    )
    assert unsupported.status_code == 400

    # Empty content_id test
    buffer = _create_image()
    missing = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("image.png", buffer, "image/png")},
        data={"content_type": "user", "content_id": "", "is_main": "false"},
    )
    # Empty string is caught by Pydantic validation (422) before our sanitization (400)
    assert missing.status_code in [400, 422]

    # Cleanup file to avoid leaking artifacts in test runs
    for path in created_paths:
        if path.exists():
            path.unlink()
            try:
                path.parent.rmdir()
            except OSError:
                pass
