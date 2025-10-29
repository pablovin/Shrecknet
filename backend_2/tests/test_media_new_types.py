from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import get_settings
from app.models.user import UserRole


def _create_image() -> BytesIO:
    image = Image.new("RGB", (200, 200), color="blue")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


@pytest.mark.asyncio
async def test_upload_library_image(client):
    """Test uploading images for library model type."""
    # Create admin user
    admin_payload = {
        "username": "library-admin",
        "password": "LibraryAdmin123",
        "full_name": "Library Admin",
        "email": "library-admin@example.com",
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

    # Create ontology
    ontology_response = await client.post(
        "/ontologies/",
        json={"name": "Library Test Ontology", "description": "Test library items"},
        headers=admin_headers,
    )
    assert ontology_response.status_code == 201, ontology_response.text
    ontology_id = ontology_response.json()["id"]

    # Create library item
    pdf_content = b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n>>\nendobj\n"
    library_response = await client.post(
        f"/libraries/{ontology_id}/items",
        headers=admin_headers,
        files={"file": ("test.pdf", BytesIO(pdf_content), "application/pdf")},
        data={"title": "Test Library Item", "description": "Test description"},
    )
    assert library_response.status_code == 201, library_response.text
    library_id = str(library_response.json()["id"])

    # Upload image for library item
    buffer = _create_image()
    upload_response = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("library.png", buffer, "image/png")},
        data={"content_type": "library", "content_id": library_id, "is_main": "true"},
    )
    assert upload_response.status_code == 201, upload_response.text
    url = upload_response.json()["url"]
    settings = get_settings()
    base_url = (
        settings.media_public_url.rstrip("/")
        if settings.media_public_url
        else settings.media_base_url.rstrip("/")
    )
    assert url == f"{base_url}/library/{library_id}/file.png"

    # Verify file exists
    media_root = Path(settings.media_root)
    image_path = media_root / "library" / library_id / "file.png"
    assert image_path.exists()

    # Cleanup
    if image_path.exists():
        image_path.unlink()
        try:
            image_path.parent.rmdir()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_upload_content_image(client):
    """Test uploading images for content model type."""
    # Create admin user
    admin_payload = {
        "username": "content-admin",
        "password": "ContentAdmin123",
        "full_name": "Content Admin",
        "email": "content-admin@example.com",
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

    # Upload image for content (no validation, just accepts content_id)
    buffer = _create_image()
    upload_response = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("content.png", buffer, "image/png")},
        data={"content_type": "content", "content_id": "content-123", "is_main": "true"},
    )
    assert upload_response.status_code == 201, upload_response.text
    url = upload_response.json()["url"]
    settings = get_settings()
    base_url = (
        settings.media_public_url.rstrip("/")
        if settings.media_public_url
        else settings.media_base_url.rstrip("/")
    )
    assert url == f"{base_url}/content/content-123/file.png"

    # Verify file exists
    media_root = Path(settings.media_root)
    image_path = media_root / "content" / "content-123" / "file.png"
    assert image_path.exists()

    # Cleanup
    if image_path.exists():
        image_path.unlink()
        try:
            image_path.parent.rmdir()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_upload_agent_image(client):
    """Test uploading images for agent model type."""
    # Create admin user
    admin_payload = {
        "username": "agent-admin",
        "password": "AgentAdmin123",
        "full_name": "Agent Admin",
        "email": "agent-admin@example.com",
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

    # Upload image for agent (no validation, just accepts content_id)
    buffer = _create_image()
    upload_response = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("agent.png", buffer, "image/png")},
        data={"content_type": "agent", "content_id": "agent-456", "is_main": "true"},
    )
    assert upload_response.status_code == 201, upload_response.text
    url = upload_response.json()["url"]
    settings = get_settings()
    base_url = (
        settings.media_public_url.rstrip("/")
        if settings.media_public_url
        else settings.media_base_url.rstrip("/")
    )
    assert url == f"{base_url}/agent/agent-456/file.png"

    # Verify file exists
    media_root = Path(settings.media_root)
    image_path = media_root / "agent" / "agent-456" / "file.png"
    assert image_path.exists()

    # Cleanup
    if image_path.exists():
        image_path.unlink()
        try:
            image_path.parent.rmdir()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_upload_empty_instance_id_rejection(client):
    """Test that empty instance_id is rejected for content and agent types."""
    # Create admin user
    admin_payload = {
        "username": "validation-admin",
        "password": "ValidationAdmin123",
        "full_name": "Validation Admin",
        "email": "validation-admin@example.com",
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

    # Test empty content_id for content
    buffer = _create_image()
    upload_response = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("content.png", buffer, "image/png")},
        data={"content_type": "content", "content_id": "", "is_main": "false"},
    )
    # FastAPI returns 422 for empty form fields before custom validation
    assert upload_response.status_code in (400, 422)

    # Test empty content_id for agent
    buffer = _create_image()
    upload_response = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("agent.png", buffer, "image/png")},
        data={"content_type": "agent", "content_id": "   ", "is_main": "false"},
    )
    # FastAPI returns 422 for empty form fields before custom validation
    assert upload_response.status_code in (400, 422)
