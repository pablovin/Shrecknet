"""Tests for ontology embedding statistics and tracking."""

from __future__ import annotations

import os

import pytest

from app.models.ontology import AuthorType, Cardinality, PropertyDataType
from app.models.user import UserRole

pytestmark = pytest.mark.skipif(
    not os.getenv("NEO4J_TEST_URI"),
    reason="Neo4j test environment not configured",
)


@pytest.mark.asyncio
async def test_embedding_stats_with_instances(client):
    """Test that embedding stats correctly count instances."""
    # Bootstrap admin user
    admin_payload = {
        "username": "embedding-admin",
        "password": "EmbedAdmin1",
        "full_name": "Embedding Admin",
        "email": "embed-admin@example.com",
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

    # Create ontology definition
    ontology_response = await client.post(
        "/ontologies/",
        json={"name": "Embedding Test Ontology", "description": "Test ontology"},
        headers=headers,
    )
    assert ontology_response.status_code == 201, ontology_response.text
    ontology_id = ontology_response.json()["id"]

    # Create entity definition
    entity_payload = {
        "name": "TestEntity",
        "description": "Test entity definition",
        "auto_generatable": False,
        "author_type": AuthorType.HUMAN.value,
        "user_id": "test-user-1",
    }
    entity_response = await client.post(
        f"/ontologies/{ontology_id}/entities",
        json=entity_payload,
        headers=headers,
    )
    assert entity_response.status_code == 201, entity_response.text
    entity_id = entity_response.json()["id"]

    # Add a property to the entity
    property_payload = {
        "name": "TestProp",
        "cardinality": Cardinality.ONE.value,
        "data_type": PropertyDataType.TEXT.value,
        "author_type": AuthorType.HUMAN.value,
        "user_id": "test-user-1",
    }
    property_response = await client.post(
        f"/ontologies/{ontology_id}/entities/{entity_id}/properties",
        json=property_payload,
        headers=headers,
    )
    assert property_response.status_code == 201, property_response.text
    property_id = property_response.json()["id"]

    # Check initial embedding stats (should be all zeros)
    stats_response = await client.get(
        f"/ontologies/{ontology_id}/embedding-stats",
        headers=headers,
    )
    assert stats_response.status_code == 200, stats_response.text
    stats = stats_response.json()
    assert stats["ontology_id"] == ontology_id
    assert stats["total_nodes"] == 0
    assert stats["embedded_nodes"] == 0
    assert stats["unembedded_nodes"] == 0
    assert stats["outdated_nodes"] == 0

    # Create ontology instance with 4 entities
    instance_payload = {
        "ontology_id": ontology_id,
        "name": "Test Instance",
        "description": "Instance with 4 entities",
        "entities": [
            {
                "definition_id": entity_id,
                "alias": f"entity_{i}",
                "text": f"Entity {i} text",
                "author_type": AuthorType.HUMAN.value,
                "author_id": "test-user",
                "properties": [{"definition_id": property_id, "value": f"Value {i}"}],
                "relationships": [],
            }
            for i in range(1, 5)  # Create 4 entities
        ],
    }
    instance_response = await client.post(
        "/ontology-instances/", json=instance_payload, headers=headers
    )
    assert instance_response.status_code == 201, instance_response.text
    instance = instance_response.json()
    instance_id = instance["instance_id"]

    # Check embedding stats again (should now show 4 total nodes, all unembedded)
    stats_response = await client.get(
        f"/ontologies/{ontology_id}/embedding-stats",
        headers=headers,
    )
    assert stats_response.status_code == 200, stats_response.text
    stats = stats_response.json()
    assert stats["ontology_id"] == ontology_id
    assert stats["total_nodes"] == 4, f"Expected 4 total nodes, got {stats['total_nodes']}"
    assert stats["embedded_nodes"] == 0
    assert stats["unembedded_nodes"] == 4, f"Expected 4 unembedded nodes, got {stats['unembedded_nodes']}"
    assert stats["outdated_nodes"] == 0

    # Verify the instances are created with is_embedded = false
    fetched = await client.get(f"/ontology-instances/{instance_id}", headers=headers)
    assert fetched.status_code == 200
    fetched_data = fetched.json()
    assert len(fetched_data["entities"]) == 4

    # Clean up
    delete_response = await client.delete(
        f"/ontology-instances/{instance_id}", headers=headers
    )
    assert delete_response.status_code == 204

    # Verify stats are back to zero
    stats_response = await client.get(
        f"/ontologies/{ontology_id}/embedding-stats",
        headers=headers,
    )
    assert stats_response.status_code == 200
    stats = stats_response.json()
    assert stats["total_nodes"] == 0


@pytest.mark.asyncio
async def test_embedding_stats_multiple_ontologies(client):
    """Test that embedding stats correctly filter by ontology_id."""
    # Bootstrap admin user
    admin_payload = {
        "username": "multi-onto-admin",
        "password": "MultiOnto1",
        "full_name": "Multi Onto Admin",
        "email": "multi-onto@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    register_response = await client.post("/users/", json=admin_payload)
    assert register_response.status_code == 201

    token_response = await client.post(
        "/auth/token",
        data={
            "username": admin_payload["username"],
            "password": admin_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200
    token = token_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create two ontologies
    ontology1_response = await client.post(
        "/ontologies/",
        json={"name": "Ontology 1", "description": "First ontology"},
        headers=headers,
    )
    assert ontology1_response.status_code == 201
    ontology1_id = ontology1_response.json()["id"]

    ontology2_response = await client.post(
        "/ontologies/",
        json={"name": "Ontology 2", "description": "Second ontology"},
        headers=headers,
    )
    assert ontology2_response.status_code == 201
    ontology2_id = ontology2_response.json()["id"]

    # Create entity for ontology 1
    entity1_response = await client.post(
        f"/ontologies/{ontology1_id}/entities",
        json={
            "name": "Entity1",
            "description": "Entity 1",
            "auto_generatable": False,
            "author_type": AuthorType.HUMAN.value,
            "user_id": "user-1",
        },
        headers=headers,
    )
    assert entity1_response.status_code == 201
    entity1_id = entity1_response.json()["id"]

    # Create entity for ontology 2
    entity2_response = await client.post(
        f"/ontologies/{ontology2_id}/entities",
        json={
            "name": "Entity2",
            "description": "Entity 2",
            "auto_generatable": False,
            "author_type": AuthorType.HUMAN.value,
            "user_id": "user-2",
        },
        headers=headers,
    )
    assert entity2_response.status_code == 201
    entity2_id = entity2_response.json()["id"]

    # Create instance for ontology 1 with 2 entities
    instance1_response = await client.post(
        "/ontology-instances/",
        json={
            "ontology_id": ontology1_id,
            "name": "Instance 1",
            "entities": [
                {
                    "definition_id": entity1_id,
                    "alias": f"e1_{i}",
                    "text": f"Text {i}",
                    "author_type": AuthorType.HUMAN.value,
                    "author_id": "user",
                    "properties": [],
                    "relationships": [],
                }
                for i in range(2)
            ],
        },
        headers=headers,
    )
    assert instance1_response.status_code == 201
    instance1_id = instance1_response.json()["instance_id"]

    # Create instance for ontology 2 with 3 entities
    instance2_response = await client.post(
        "/ontology-instances/",
        json={
            "ontology_id": ontology2_id,
            "name": "Instance 2",
            "entities": [
                {
                    "definition_id": entity2_id,
                    "alias": f"e2_{i}",
                    "text": f"Text {i}",
                    "author_type": AuthorType.HUMAN.value,
                    "author_id": "user",
                    "properties": [],
                    "relationships": [],
                }
                for i in range(3)
            ],
        },
        headers=headers,
    )
    assert instance2_response.status_code == 201
    instance2_id = instance2_response.json()["instance_id"]

    # Check stats for ontology 1 (should show 2 nodes)
    stats1_response = await client.get(
        f"/ontologies/{ontology1_id}/embedding-stats",
        headers=headers,
    )
    assert stats1_response.status_code == 200
    stats1 = stats1_response.json()
    assert stats1["ontology_id"] == ontology1_id
    assert stats1["total_nodes"] == 2, f"Expected 2 nodes for ontology 1, got {stats1['total_nodes']}"
    assert stats1["unembedded_nodes"] == 2

    # Check stats for ontology 2 (should show 3 nodes)
    stats2_response = await client.get(
        f"/ontologies/{ontology2_id}/embedding-stats",
        headers=headers,
    )
    assert stats2_response.status_code == 200
    stats2 = stats2_response.json()
    assert stats2["ontology_id"] == ontology2_id
    assert stats2["total_nodes"] == 3, f"Expected 3 nodes for ontology 2, got {stats2['total_nodes']}"
    assert stats2["unembedded_nodes"] == 3

    # Clean up
    await client.delete(f"/ontology-instances/{instance1_id}", headers=headers)
    await client.delete(f"/ontology-instances/{instance2_id}", headers=headers)
