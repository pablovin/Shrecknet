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

    # Upload image for content type
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

    # Upload image for agent type
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

    # Test validation rejects empty content_id
    buffer = _create_image()
    upload_response = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("content.png", buffer, "image/png")},
        data={"content_type": "content", "content_id": "", "is_main": "false"},
    )
    # FastAPI returns 422 for empty form fields before custom validation
    assert upload_response.status_code in (400, 422)

    # Test validation rejects whitespace-only content_id
    buffer = _create_image()
    upload_response = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("agent.png", buffer, "image/png")},
        data={"content_type": "agent", "content_id": "   ", "is_main": "false"},
    )
    # FastAPI returns 422 for empty form fields before custom validation
    assert upload_response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_upload_property_pdf(client):
    """Test uploading a PDF for a property content record."""
    admin_payload = {
        "username": "pdf-admin",
        "password": "PdfAdmin123",
        "full_name": "PDF Admin",
        "email": "pdf-admin@example.com",
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

    pdf_content = b"%PDF-1.4\n%created for testing\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    upload_response = await client.post(
        "/media-admin/pdfs",
        headers=admin_headers,
        files={"file": ("Lore Document.pdf", BytesIO(pdf_content), "application/pdf")},
        data={"content_id": "property-789"},
    )
    assert upload_response.status_code == 201, upload_response.text
    url = upload_response.json()["url"]

    settings = get_settings()
    base_url = (
        settings.media_public_url.rstrip("/")
        if settings.media_public_url
        else settings.media_base_url.rstrip("/")
    )
    expected_filename = "Lore-Document.pdf"
    assert url == f"{base_url}/content/property-789/{expected_filename}"

    media_root = Path(settings.media_root)
    pdf_path = media_root / "content" / "property-789" / expected_filename
    assert pdf_path.exists()

    if pdf_path.exists():
        pdf_path.unlink()
        try:
            pdf_path.parent.rmdir()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_upload_property_pdf_rejects_non_pdf(client):
    """Ensure invalid PDF uploads are rejected."""
    admin_payload = {
        "username": "pdf-invalid-admin",
        "password": "PdfInvalid123",
        "full_name": "PDF Invalid Admin",
        "email": "pdf-invalid-admin@example.com",
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

    bogus_pdf = b"This is not a pdf file"
    upload_response = await client.post(
        "/media-admin/pdfs",
        headers=admin_headers,
        files={"file": ("fake.pdf", BytesIO(bogus_pdf), "application/pdf")},
        data={"content_id": "property-999"},
    )
    assert upload_response.status_code == 400, upload_response.text
    assert "valid PDF" in upload_response.json()["detail"]
