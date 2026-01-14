from __future__ import annotations

import os

import pytest

from app.models.user import UserRole


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("NEO4J_TEST_URI"),
    reason="Neo4j test environment not configured",
)
async def test_favorite_ontology_instances_basic_flow(client):
    """Test basic favorite operations without Neo4j dependency."""
    # Create admin user
    admin_payload = {
        "username": "fav-admin",
        "password": "FavAdmin123",
        "full_name": "Favorite Admin",
        "email": "fav-admin@example.com",
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

    # Create regular user
    user_payload = {
        "username": "fav-user",
        "password": "FavUser123",
        "full_name": "Favorite User",
        "email": "fav-user@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    user_register = await client.post("/users/", json=user_payload)
    assert user_register.status_code == 201, user_register.text

    user_token_response = await client.post(
        "/auth/token",
        data={
            "username": user_payload["username"],
            "password": user_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert user_token_response.status_code == 200, user_token_response.text
    user_token = user_token_response.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {user_token}"}

    # Create ontology
    ontology_payload = {
        "name": "Test Favorite Ontology",
        "description": "Test ontology for favorites",
    }
    ontology_response = await client.post(
        "/ontologies/", json=ontology_payload, headers=admin_headers
    )
    assert ontology_response.status_code == 201, ontology_response.text
    ontology_id = ontology_response.json()["id"]

    # Test: List favorites (should be empty)
    list_response = await client.get(
        "/ontology-instances/favorites", headers=user_headers
    )
    assert list_response.status_code == 200, list_response.text
    assert list_response.json() == []

    # Test: Try to favorite a non-existent instance (should fail)
    fake_instance_id = "fake-instance-123"
    favorite_payload = {"ontology_id": ontology_id}
    add_fake_response = await client.post(
        f"/ontology-instances/{fake_instance_id}/favorite",
        json=favorite_payload,
        headers=user_headers,
    )
    assert add_fake_response.status_code == 404, add_fake_response.text

    # Test: Check is_favorite for non-existent instance
    is_fav_response = await client.get(
        f"/ontology-instances/{fake_instance_id}/is-favorite",
        headers=user_headers,
    )
    assert is_fav_response.status_code == 200, is_fav_response.text
    assert is_fav_response.json()["is_favorite"] is False

    # Test: Remove non-existent favorite (should return 404)
    remove_fake_response = await client.delete(
        f"/ontology-instances/{fake_instance_id}/favorite",
        headers=user_headers,
    )
    assert remove_fake_response.status_code == 404, remove_fake_response.text


@pytest.mark.asyncio
async def test_favorite_permissions(client):
    """Test that list favorites requires authentication."""
    # Try to list favorites without auth  
    list_response = await client.get("/ontology-instances/favorites")
    assert list_response.status_code == 401


@pytest.mark.asyncio
async def test_favorite_requires_authentication(client):
    """Test that favorite endpoints require authentication."""
    fake_instance_id = "test-instance-123"

    # Try without auth
    list_response = await client.get("/ontology-instances/favorites")
    assert list_response.status_code == 401

    is_fav_response = await client.get(
        f"/ontology-instances/{fake_instance_id}/is-favorite"
    )
    assert is_fav_response.status_code == 401

    add_response = await client.post(
        f"/ontology-instances/{fake_instance_id}/favorite",
        json={"ontology_id": 1},
    )
    assert add_response.status_code == 401

    remove_response = await client.delete(
        f"/ontology-instances/{fake_instance_id}/favorite"
    )
    assert remove_response.status_code == 401
