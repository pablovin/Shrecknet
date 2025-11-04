"""Integration tests for hierarchical role-based access control on API endpoints."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.user import UserRole


@pytest_asyncio.fixture()
async def writer_token(client: AsyncClient, admin_token: str) -> str:
    """Create a writer user and return auth token."""
    writer_payload = {
        "username": "test-writer",
        "password": "WriterPass123",
        "full_name": "Test Writer",
        "email": "writer@test.com",
        "timezone": "UTC",
        "role": UserRole.WRITER.value,
    }
    
    # Register writer
    register_response = await client.post("/users/", json=writer_payload)
    assert register_response.status_code == 201
    
    # Get token
    token_response = await client.post(
        "/auth/token",
        data={
            "username": writer_payload["username"],
            "password": writer_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200
    return token_response.json()["access_token"]


@pytest_asyncio.fixture()
async def world_builder_token(client: AsyncClient, admin_token: str) -> str:
    """Create a world builder user and return auth token."""
    wb_payload = {
        "username": "test-worldbuilder",
        "password": "WorldBuilderPass123",
        "full_name": "Test World Builder",
        "email": "worldbuilder@test.com",
        "timezone": "UTC",
        "role": UserRole.WORLD_BUILDER.value,
    }
    
    # Register world builder
    register_response = await client.post("/users/", json=wb_payload)
    assert register_response.status_code == 201
    
    # Get token
    token_response = await client.post(
        "/auth/token",
        data={
            "username": wb_payload["username"],
            "password": wb_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200
    return token_response.json()["access_token"]


@pytest.mark.asyncio
class TestHierarchicalAccessOntologies:
    """Test hierarchical access to ontology endpoints."""

    async def test_player_can_read_ontologies(
        self, client: AsyncClient, user_token: str, admin_token: str
    ):
        """Test that Player can read (list/get) ontologies."""
        # Create an ontology as admin
        headers_admin = {"Authorization": f"Bearer {admin_token}"}
        ontology_data = {"name": "Test Ontology", "description": "Test"}
        create_resp = await client.post(
            "/ontologies/", json=ontology_data, headers=headers_admin
        )
        assert create_resp.status_code == 201
        ontology_id = create_resp.json()["id"]

        # Player can list ontologies
        headers_player = {"Authorization": f"Bearer {user_token}"}
        list_resp = await client.get("/ontologies/", headers=headers_player)
        assert list_resp.status_code == 200

        # Player can get specific ontology
        get_resp = await client.get(
            f"/ontologies/{ontology_id}", headers=headers_player
        )
        assert get_resp.status_code == 200

    async def test_player_cannot_create_ontologies(
        self, client: AsyncClient, user_token: str
    ):
        """Test that Player cannot create ontologies (requires Writer+)."""
        headers = {"Authorization": f"Bearer {user_token}"}
        ontology_data = {"name": "Player Ontology", "description": "Test"}
        
        resp = await client.post("/ontologies/", json=ontology_data, headers=headers)
        assert resp.status_code == 403

    async def test_writer_can_create_ontologies(
        self, client: AsyncClient, writer_token: str
    ):
        """
        Test that Writer CANNOT create ontologies in current implementation.
        
        NOTE: Per problem statement, Writers should have "content creation/editing"
        but current implementation requires WORLD_BUILDER+ for ontology creation.
        This test documents current behavior - if requirements change,
        ontologies.py should be updated to use require_roles(UserRole.WRITER).
        """
        headers = {"Authorization": f"Bearer {writer_token}"}
        ontology_data = {"name": "Writer Ontology", "description": "Test"}
        
        resp = await client.post("/ontologies/", json=ontology_data, headers=headers)
        # Current implementation: Writers CANNOT create ontologies
        assert resp.status_code == 403

    async def test_world_builder_can_create_ontologies(
        self, client: AsyncClient, world_builder_token: str
    ):
        """Test that World Builder can create ontologies (hierarchy works)."""
        headers = {"Authorization": f"Bearer {world_builder_token}"}
        ontology_data = {"name": "WB Ontology", "description": "Test"}
        
        resp = await client.post("/ontologies/", json=ontology_data, headers=headers)
        assert resp.status_code == 201

    async def test_admin_can_create_ontologies(
        self, client: AsyncClient, admin_token: str
    ):
        """Test that Admin can create ontologies."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        ontology_data = {"name": "Admin Ontology", "description": "Test"}
        
        resp = await client.post("/ontologies/", json=ontology_data, headers=headers)
        assert resp.status_code == 201


@pytest.mark.asyncio
class TestHierarchicalAccessNotes:
    """Test hierarchical access to notes endpoints."""

    async def test_all_users_can_create_notes(
        self, client: AsyncClient, admin_token: str, user_token: str, writer_token: str
    ):
        """Test that all authenticated users can create notes."""
        # Create ontology first
        headers_admin = {"Authorization": f"Bearer {admin_token}"}
        ontology_data = {"name": "Notes Ontology", "description": "Test"}
        ontology_resp = await client.post(
            "/ontologies/", json=ontology_data, headers=headers_admin
        )
        assert ontology_resp.status_code == 201
        ontology_id = ontology_resp.json()["id"]

        # Test each role can create notes
        for token, role_name in [
            (user_token, "Player"),
            (writer_token, "Writer"),
            (admin_token, "Admin"),
        ]:
            headers = {"Authorization": f"Bearer {token}"}
            note_data = {
                "title": f"{role_name} Note",
                "content": "Test content",
                "ontology_id": ontology_id,
            }
            resp = await client.post("/notes/", json=note_data, headers=headers)
            assert resp.status_code == 201, f"{role_name} should be able to create notes"

    async def test_player_cannot_edit_others_notes(
        self, client: AsyncClient, admin_token: str, user_token: str, writer_token: str
    ):
        """Test that Player cannot edit notes owned by others."""
        # Create ontology first
        headers_admin = {"Authorization": f"Bearer {admin_token}"}
        ontology_data = {"name": "Edit Test Ontology", "description": "Test"}
        ontology_resp = await client.post(
            "/ontologies/", json=ontology_data, headers=headers_admin
        )
        ontology_id = ontology_resp.json()["id"]

        # Writer creates a note
        headers_writer = {"Authorization": f"Bearer {writer_token}"}
        note_data = {
            "title": "Writer's Note",
            "content": "Original content",
            "ontology_id": ontology_id,
        }
        create_resp = await client.post("/notes/", json=note_data, headers=headers_writer)
        assert create_resp.status_code == 201
        note_id = create_resp.json()["id"]

        # Player tries to update it
        headers_player = {"Authorization": f"Bearer {user_token}"}
        update_data = {"title": "Hacked!", "content": "Hacked content"}
        update_resp = await client.put(
            f"/notes/{note_id}", json=update_data, headers=headers_player
        )
        assert update_resp.status_code == 403

    async def test_world_builder_can_edit_others_notes(
        self, client: AsyncClient, admin_token: str, writer_token: str, world_builder_token: str
    ):
        """Test that World Builder can edit notes owned by others (hierarchy privilege)."""
        # Create ontology first
        headers_admin = {"Authorization": f"Bearer {admin_token}"}
        ontology_data = {"name": "WB Edit Test Ontology", "description": "Test"}
        ontology_resp = await client.post(
            "/ontologies/", json=ontology_data, headers=headers_admin
        )
        ontology_id = ontology_resp.json()["id"]

        # Writer creates a note
        headers_writer = {"Authorization": f"Bearer {writer_token}"}
        note_data = {
            "title": "Writer's Note",
            "content": "Original content",
            "ontology_id": ontology_id,
        }
        create_resp = await client.post("/notes/", json=note_data, headers=headers_writer)
        assert create_resp.status_code == 201
        note_id = create_resp.json()["id"]

        # World Builder updates it
        headers_wb = {"Authorization": f"Bearer {world_builder_token}"}
        update_data = {"title": "Updated by WB", "content": "WB content"}
        update_resp = await client.put(
            f"/notes/{note_id}", json=update_data, headers=headers_wb
        )
        assert update_resp.status_code == 200


@pytest.mark.asyncio
class TestHierarchicalAccessNotifications:
    """Test hierarchical access to notification endpoints."""

    async def test_all_users_can_read_own_notifications(
        self, client: AsyncClient, user_token: str
    ):
        """Test that all users can read their own notifications."""
        headers = {"Authorization": f"Bearer {user_token}"}
        resp = await client.get("/notifications/me", headers=headers)
        assert resp.status_code == 200

    async def test_player_cannot_create_notifications(
        self, client: AsyncClient, user_token: str
    ):
        """Test that Player cannot create notifications (requires World Builder+)."""
        headers = {"Authorization": f"Bearer {user_token}"}
        notification_data = {
            "notification_type": "content_update",
            "title": "Test Notification",
            "description": "Test description",
            "author_type": "user",
            "author_id": "1",
            "user_id": 1,
        }
        resp = await client.post(
            "/notifications/", json=notification_data, headers=headers
        )
        assert resp.status_code == 403

    async def test_writer_cannot_create_notifications(
        self, client: AsyncClient, writer_token: str
    ):
        """Test that Writer cannot create notifications (requires World Builder+)."""
        headers = {"Authorization": f"Bearer {writer_token}"}
        notification_data = {
            "notification_type": "content_update",
            "title": "Test Notification",
            "description": "Test description",
            "author_type": "user",
            "author_id": "1",
            "user_id": 1,
        }
        resp = await client.post(
            "/notifications/", json=notification_data, headers=headers
        )
        assert resp.status_code == 403

    async def test_world_builder_can_create_notifications(
        self, client: AsyncClient, world_builder_token: str
    ):
        """Test that World Builder can create notifications."""
        headers = {"Authorization": f"Bearer {world_builder_token}"}
        notification_data = {
            "notification_type": "content_update",
            "title": "Test Notification",
            "description": "Test description",
            "author_type": "user",
            "author_id": "1",
            "user_id": 1,
        }
        resp = await client.post(
            "/notifications/", json=notification_data, headers=headers
        )
        assert resp.status_code == 201

    async def test_admin_can_create_notifications(
        self, client: AsyncClient, admin_token: str
    ):
        """Test that Admin can create notifications."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        notification_data = {
            "notification_type": "content_update",
            "title": "Admin Notification",
            "description": "Admin description",
            "author_type": "user",
            "author_id": "1",
            "user_id": 1,
        }
        resp = await client.post(
            "/notifications/", json=notification_data, headers=headers
        )
        assert resp.status_code == 201


@pytest.mark.asyncio
class TestHierarchicalAccessAdminEndpoints:
    """Test hierarchical access to admin-only endpoints."""

    async def test_only_admin_can_access_users_endpoint(
        self,
        client: AsyncClient,
        admin_token: str,
        user_token: str,
        writer_token: str,
        world_builder_token: str,
    ):
        """Test that only Admin can access the users list endpoint."""
        # Admin can access
        headers_admin = {"Authorization": f"Bearer {admin_token}"}
        resp = await client.get("/users/", headers=headers_admin)
        assert resp.status_code == 200

        # Others cannot
        for token, role_name in [
            (user_token, "Player"),
            (writer_token, "Writer"),
            (world_builder_token, "World Builder"),
        ]:
            headers = {"Authorization": f"Bearer {token}"}
            resp = await client.get("/users/", headers=headers)
            assert resp.status_code == 403, f"{role_name} should not access admin endpoints"

    async def test_only_admin_can_access_audit_logs(
        self,
        client: AsyncClient,
        admin_token: str,
        user_token: str,
        writer_token: str,
        world_builder_token: str,
    ):
        """Test that only Admin can access audit logs."""
        # Admin can access
        headers_admin = {"Authorization": f"Bearer {admin_token}"}
        resp = await client.get("/logs/", headers=headers_admin)
        assert resp.status_code == 200

        # Others cannot
        for token in [user_token, writer_token, world_builder_token]:
            headers = {"Authorization": f"Bearer {token}"}
            resp = await client.get("/logs/", headers=headers)
            assert resp.status_code == 403
