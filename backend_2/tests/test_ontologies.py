from __future__ import annotations

import pytest

from app.models.ontology import AuthorType, Cardinality, PropertyDataType
from app.models.user import UserRole


@pytest.mark.asyncio
async def test_create_and_manage_ontology(client):
    admin_payload = {
        "username": "admin",
        "password": "StrongPass123",
        "full_name": "System Admin",
        "email": "admin@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    register_response = await client.post("/users/", json=admin_payload)
    assert register_response.status_code == 201, register_response.text

    token_response = await client.post(
        "/auth/token",
        data={
            "username": admin_payload["username"],
            "password": admin_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200, token_response.text
    token = token_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create ontology
    response = await client.post(
        "/ontologies/",
        json={"name": "Test Ontology", "description": "Initial description"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    ontology = response.json()
    ontology_id = ontology["id"]

    # Ensure listing works with filters
    list_response = await client.get(
        "/ontologies/", params={"name": "Test"}, headers=headers
    )
    assert list_response.status_code == 200
    assert any(item["id"] == ontology_id for item in list_response.json())

    # Add entity
    entity_payload = {
        "name": "Hero",
        "description": "Main hero",
        "keywords": ["hero", "main"],
        "auto_generatable": True,
        "author_type": AuthorType.HUMAN.value,
        "user_id": "user-123",
    }
    entity_response = await client.post(
        f"/ontologies/{ontology_id}/entities", json=entity_payload, headers=headers
    )
    assert entity_response.status_code == 201, entity_response.text
    entity_id = entity_response.json()["id"]

    # Update entity
    update_response = await client.put(
        f"/ontologies/{ontology_id}/entities/{entity_id}",
        json={"description": "Updated description"},
        headers=headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["description"] == "Updated description"

    # Add property
    property_payload = {
        "name": "Strength",
        "cardinality": Cardinality.ONE.value,
        "data_type": PropertyDataType.NUMBER.value,
        "auto_generatable": False,
        "author_type": AuthorType.AGENT.value,
        "agent_id": "agent-1",
    }
    property_response = await client.post(
        f"/ontologies/{ontology_id}/properties", json=property_payload, headers=headers,
    )
    assert property_response.status_code == 201, property_response.text
    property_id = property_response.json()["id"]

    # Add relationship
    relationship_payload = {
        "name": "Mentorship",
        "bi_directional": True,
        "author_type": AuthorType.AGENT.value,
        "agent_id": "agent-2",
    }
    relationship_response = await client.post(
        f"/ontologies/{ontology_id}/relationships",
        json=relationship_payload,
        headers=headers,
    )
    assert relationship_response.status_code == 201, relationship_response.text
    relationship_id = relationship_response.json()["id"]

    # Delete property and relationship
    delete_property = await client.delete(
        f"/ontologies/{ontology_id}/properties/{property_id}", headers=headers,
    )
    assert delete_property.status_code == 204

    delete_relationship = await client.delete(
        f"/ontologies/{ontology_id}/relationships/{relationship_id}", headers=headers,
    )
    assert delete_relationship.status_code == 204

    # Delete ontology
    delete_response = await client.delete(f"/ontologies/{ontology_id}", headers=headers)
    assert delete_response.status_code == 204

    # Ensure ontology no longer exists
    get_response = await client.get(f"/ontologies/{ontology_id}", headers=headers)
    assert get_response.status_code == 404
