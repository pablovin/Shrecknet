"""Tests for admin clear endpoints."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.models.user import UserRole


@pytest.mark.asyncio
async def test_clear_library_item_embeddings(client: AsyncClient):
    """Test clearing embeddings for a specific library item."""
    # Mock Neo4j driver to avoid connection issues in tests
    with patch("app.graph.neo4j.get_driver") as mock_driver:
        mock_session = AsyncMock()
        mock_driver.return_value.session.return_value.__aenter__.return_value = (
            mock_session
        )

        # Mock the delete_embeddings method to return a count
        async def mock_delete_embeddings(library_item_id):
            return 10

        with patch.object(
            mock_session,
            "run",
            side_effect=[
                # First call for delete_embeddings
                AsyncMock(single=AsyncMock(return_value={"total": 10}))
            ],
        ):
            # Create admin user
            admin_payload = {
                "username": "admin-clear-test",
                "password": "AdminClear123",
                "full_name": "Admin Clear Test",
                "email": "admin-clear@example.com",
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
            ontology = await client.post(
                "/ontologies/",
                json={
                    "name": "Test Ontology",
                    "description": "Test ontology for clear embeddings",
                },
                headers=admin_headers,
            )
            assert ontology.status_code == 201
            ontology_id = ontology.json()["id"]

            # Create a library item
            pdf_content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
            files = {"file": ("test.pdf", BytesIO(pdf_content), "application/pdf")}
            data = {
                "title": "Test Book",
                "authors": "Test Author",
                "description": "A test book",
            }

            response = await client.post(
                f"/libraries/{ontology_id}/items",
                files=files,
                data=data,
                headers=admin_headers,
            )
            assert response.status_code == 201
            item_id = response.json()["id"]

            # Clear embeddings for the library item
            clear_response = await client.delete(
                f"/libraries/{ontology_id}/items/{item_id}/embeddings",
                headers=admin_headers,
            )
            assert clear_response.status_code == 200
            clear_data = clear_response.json()
            assert clear_data["library_item_id"] == item_id
            assert clear_data["ontology_id"] == ontology_id
            assert "chunks_deleted" in clear_data

            # Verify the item is marked as not vectorized
            item_response = await client.get(
                f"/libraries/{ontology_id}/items/{item_id}",
                headers=admin_headers,
            )
            assert item_response.status_code == 200
            item_data = item_response.json()
            assert item_data["vectorized"] is False
            assert item_data["last_vectorized_at"] is None


@pytest.mark.asyncio
async def test_clear_library_item_embeddings_requires_admin(client: AsyncClient):
    """Test that clearing embeddings requires admin role."""
    # Create admin user first to create ontology and library item
    admin_payload = {
        "username": "admin-create-item",
        "password": "AdminCreate123",
        "full_name": "Admin Create",
        "email": "admin-create@example.com",
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
    ontology = await client.post(
        "/ontologies/",
        json={
            "name": "Test Ontology Perm",
            "description": "Test ontology for permissions",
        },
        headers=admin_headers,
    )
    assert ontology.status_code == 201
    ontology_id = ontology.json()["id"]

    # Create a library item
    pdf_content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    files = {"file": ("test.pdf", BytesIO(pdf_content), "application/pdf")}
    data = {
        "title": "Test Book",
        "authors": "Test Author",
        "description": "A test book",
    }

    response = await client.post(
        f"/libraries/{ontology_id}/items",
        files=files,
        data=data,
        headers=admin_headers,
    )
    assert response.status_code == 201
    item_id = response.json()["id"]

    # Now create player user
    player_payload = {
        "username": "player-clear-test",
        "password": "PlayerClear123",
        "full_name": "Player Clear Test",
        "email": "player-clear@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    player_register = await client.post("/users/", json=player_payload)
    assert player_register.status_code == 201
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

    # Try to clear embeddings with player role
    clear_response = await client.delete(
        f"/libraries/{ontology_id}/items/{item_id}/embeddings",
        headers=player_headers,
    )
    assert clear_response.status_code == 403


@pytest.mark.asyncio
async def test_clear_all_library_embeddings(client: AsyncClient):
    """Test clearing all library embeddings."""
    # Mock Neo4j driver to avoid connection issues in tests
    with patch("app.graph.neo4j.get_driver") as mock_driver:
        mock_session = AsyncMock()
        mock_driver.return_value.session.return_value.__aenter__.return_value = (
            mock_session
        )

        # Mock the delete_embeddings to return count
        async def mock_delete_embeddings(library_item_id):
            return 5

        with patch.object(
            mock_session,
            "run",
            side_effect=[
                # Call for delete_embeddings - return 5 chunks deleted
                AsyncMock(single=AsyncMock(return_value={"total": 5}))
            ],
        ):
            # Create admin user
            admin_payload = {
                "username": "admin-clear-all",
                "password": "AdminClearAll123",
                "full_name": "Admin Clear All",
                "email": "admin-clear-all@example.com",
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
            ontology = await client.post(
                "/ontologies/",
                json={
                    "name": "Test Ontology 2",
                    "description": "Test ontology for clear all embeddings",
                },
                headers=admin_headers,
            )
            assert ontology.status_code == 201
            ontology_id = ontology.json()["id"]

            # Clear all embeddings for the ontology
            clear_response = await client.delete(
                f"/libraries/admin/clear-all-embeddings?ontology_id={ontology_id}",
                headers=admin_headers,
            )
            assert clear_response.status_code == 200
            clear_data = clear_response.json()
            assert clear_data["ontology_id"] == ontology_id
            assert "items_affected" in clear_data
            assert "chunks_deleted" in clear_data


@pytest.mark.asyncio
async def test_clear_all_library_embeddings_requires_admin(client: AsyncClient):
    """Test that clearing all embeddings requires admin role."""
    # Create admin user first (first user gets admin automatically)
    admin_payload = {
        "username": "admin-first",
        "password": "AdminFirst123",
        "full_name": "Admin First",
        "email": "admin-first@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    await client.post("/users/", json=admin_payload)

    # Create player user
    player_payload = {
        "username": "player-clear-all",
        "password": "PlayerClearAll123",
        "full_name": "Player Clear All",
        "email": "player-clear-all@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    player_register = await client.post("/users/", json=player_payload)
    assert player_register.status_code == 201
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

    # Try to clear all embeddings with player role
    clear_response = await client.delete(
        "/libraries/admin/clear-all-embeddings",
        headers=player_headers,
    )
    assert clear_response.status_code == 403


@pytest.mark.asyncio
async def test_clear_all_background_jobs(client: AsyncClient):
    """Test clearing all background jobs."""
    # Create admin user
    admin_payload = {
        "username": "admin-jobs-clear",
        "password": "AdminJobsClear123",
        "full_name": "Admin Jobs Clear",
        "email": "admin-jobs-clear@example.com",
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

    # Clear all background jobs
    clear_response = await client.delete(
        "/jobs/admin/clear-all",
        headers=admin_headers,
    )
    assert clear_response.status_code == 200
    clear_data = clear_response.json()
    assert "deleted_count" in clear_data
    assert "message" in clear_data


@pytest.mark.asyncio
async def test_clear_all_background_jobs_with_filters(client: AsyncClient):
    """Test clearing background jobs with filters."""
    # Create admin user
    admin_payload = {
        "username": "admin-jobs-filter",
        "password": "AdminJobsFilter123",
        "full_name": "Admin Jobs Filter",
        "email": "admin-jobs-filter@example.com",
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

    # Create some background jobs using the API
    from app.schemas.background_job import BackgroundJobCreate
    from app.models.background_job import JobType, AuthorType

    job1_data = BackgroundJobCreate(
        author_type=AuthorType.USER,
        author_id="1",
        job_type=JobType.PDF_BOOK_EMBEDDING,
        description="Test job 1",
        celery_task_id="task-1",
        ontology_id=1,
    )

    job2_data = BackgroundJobCreate(
        author_type=AuthorType.USER,
        author_id="1",
        job_type=JobType.NEO4J_EMBEDDING,
        description="Test job 2",
        celery_task_id="task-2",
        ontology_id=1,
    )

    await client.post(
        "/jobs/",
        json=job1_data.model_dump(mode="json"),
        headers=admin_headers,
    )

    await client.post(
        "/jobs/",
        json=job2_data.model_dump(mode="json"),
        headers=admin_headers,
    )

    # Clear only PDF_BOOK_EMBEDDING jobs
    clear_response = await client.delete(
        f"/jobs/admin/clear-all?job_type={JobType.PDF_BOOK_EMBEDDING.value}",
        headers=admin_headers,
    )
    assert clear_response.status_code == 200
    clear_data = clear_response.json()
    assert clear_data["job_type"] == JobType.PDF_BOOK_EMBEDDING.value

    # Clear jobs by ontology
    clear_response = await client.delete(
        "/jobs/admin/clear-all?ontology_id=1",
        headers=admin_headers,
    )
    assert clear_response.status_code == 200
    clear_data = clear_response.json()
    assert clear_data["ontology_id"] == 1


@pytest.mark.asyncio
async def test_clear_all_background_jobs_requires_admin(client: AsyncClient):
    """Test that clearing all background jobs requires admin role."""
    # Create admin user first (first user gets admin automatically)
    admin_payload = {
        "username": "admin-jobs-first",
        "password": "AdminJobsFirst123",
        "full_name": "Admin Jobs First",
        "email": "admin-jobs-first@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    await client.post("/users/", json=admin_payload)

    # Create player user
    player_payload = {
        "username": "player-jobs-clear",
        "password": "PlayerJobsClear123",
        "full_name": "Player Jobs Clear",
        "email": "player-jobs-clear@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    player_register = await client.post("/users/", json=player_payload)
    assert player_register.status_code == 201
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

    # Try to clear all jobs with player role
    clear_response = await client.delete(
        "/jobs/admin/clear-all",
        headers=player_headers,
    )
    assert clear_response.status_code == 403
