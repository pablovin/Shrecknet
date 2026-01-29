from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.models.user import UserRole

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"


def create_pdf_with_metadata(title: str, author: str, subject: str) -> bytes:
    """Create a valid PDF with metadata for testing."""
    from PyPDF2 import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata(
        {
            "/Title": title,
            "/Author": author,
            "/Subject": subject,
        }
    )

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_library_item_and_bookmarks_flow(client):
    settings = get_settings()

    admin_payload = {
        "username": "lib-admin",
        "password": "LibAdmin123",
        "full_name": "Library Admin",
        "email": "lib-admin@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    admin_register = await client.post("/users/", json=admin_payload)
    assert admin_register.status_code == 201, admin_register.text
    admin_id = admin_register.json()["id"]

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

    player_payload = {
        "username": "lib-player",
        "password": "LibPlayer123",
        "full_name": "Library Player",
        "email": "lib-player@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    player_register = await client.post("/users/", json=player_payload)
    assert player_register.status_code == 201, player_register.text
    player_id = player_register.json()["id"]

    player_token_response = await client.post(
        "/auth/token",
        data={
            "username": player_payload["username"],
            "password": player_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert player_token_response.status_code == 200, player_token_response.text
    player_token = player_token_response.json()["access_token"]
    player_headers = {"Authorization": f"Bearer {player_token}"}

    ontology_response = await client.post(
        "/ontologies/",
        json={"name": "Library Ontology", "description": "Testing"},
        headers=admin_headers,
    )
    assert ontology_response.status_code == 201, ontology_response.text
    ontology_id = ontology_response.json()["id"]

    pdf_buffer = BytesIO(PDF_BYTES)
    create_response = await client.post(
        f"/libraries/{ontology_id}/items",
        headers=admin_headers,
        files={"file": ("content.pdf", pdf_buffer, "application/pdf")},
        data={
            "title": "World Guide",
            "description": "Campaign rules",
            "cover_url": "https://example.com/cover.png",
        },
    )
    assert create_response.status_code == 201, create_response.text
    item = create_response.json()
    item_id = item["id"]
    assert item["pdf_url"].endswith(f"/library/{ontology_id}/{item_id}/content.pdf")

    pdf_file_path = (
        Path(settings.media_root)
        / "library"
        / str(ontology_id)
        / str(item_id)
        / "content.pdf"
    )
    assert pdf_file_path.exists()

    list_response = await client.get(
        f"/libraries/{ontology_id}/items", headers=player_headers
    )
    assert list_response.status_code == 200
    assert any(entry["id"] == item_id for entry in list_response.json())

    bookmark_payload = {
        "page": 3,
        "title": "Important Section",
        "description": "Read before session",
        "is_private": False,
        "shared_user_ids": [admin_id],
    }
    bookmark_response = await client.post(
        f"/libraries/items/{item_id}/bookmarks",
        json=bookmark_payload,
        headers=player_headers,
    )
    assert bookmark_response.status_code == 201, bookmark_response.text
    bookmark = bookmark_response.json()
    bookmark_id = bookmark["id"]
    assert bookmark["owner"]["id"] == player_id
    assert any(user["id"] == admin_id for user in bookmark["shared_with"])

    admin_bookmarks = await client.get(
        f"/libraries/items/{item_id}/bookmarks", headers=admin_headers
    )
    assert admin_bookmarks.status_code == 200
    assert any(entry["id"] == bookmark_id for entry in admin_bookmarks.json())

    leave_share = await client.delete(
        f"/libraries/bookmarks/{bookmark_id}/share/me",
        headers=admin_headers,
    )
    assert leave_share.status_code == 200, leave_share.text
    assert all(user["id"] != admin_id for user in leave_share.json()["shared_with"])

    update_bookmark = await client.put(
        f"/libraries/bookmarks/{bookmark_id}",
        json={"is_private": True},
        headers=player_headers,
    )
    assert update_bookmark.status_code == 200, update_bookmark.text
    assert update_bookmark.json()["is_private"] is True
    assert update_bookmark.json()["shared_with"] == []

    new_pdf_buffer = BytesIO(PDF_BYTES + b"\n")
    replace_response = await client.post(
        f"/libraries/{ontology_id}/items/{item_id}/content",
        headers=admin_headers,
        files={"file": ("content.pdf", new_pdf_buffer, "application/pdf")},
    )
    assert replace_response.status_code == 200, replace_response.text

    delete_response = await client.delete(
        f"/libraries/{ontology_id}/items/{item_id}", headers=admin_headers
    )
    assert delete_response.status_code == 204, delete_response.text
    assert not pdf_file_path.exists()


@pytest.mark.asyncio
async def test_library_item_auto_extract_metadata(client):
    """Test creating a library item with auto-extraction of metadata from PDF."""
    settings = get_settings()

    # Create admin user
    admin_payload = {
        "username": "lib-admin-meta",
        "password": "LibAdmin123",
        "full_name": "Library Admin Meta",
        "email": "lib-admin-meta@example.com",
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
        json={
            "name": "Meta Library Ontology",
            "description": "Testing metadata extraction",
        },
        headers=admin_headers,
    )
    assert ontology_response.status_code == 201, ontology_response.text
    ontology_id = ontology_response.json()["id"]

    # Create a PDF with metadata
    pdf_bytes = create_pdf_with_metadata(
        title="The Great Adventure",
        author="John Doe, Jane Smith",
        subject="A comprehensive guide to world building",
    )
    pdf_buffer = BytesIO(pdf_bytes)

    # Create library item with auto_extract_metadata=True and no manual metadata
    create_response = await client.post(
        f"/libraries/{ontology_id}/items",
        headers=admin_headers,
        files={"file": ("content.pdf", pdf_buffer, "application/pdf")},
        data={
            "auto_extract_metadata": "true",
        },
    )
    assert create_response.status_code == 201, create_response.text
    item = create_response.json()
    item_id = item["id"]

    # Verify metadata was extracted
    assert item["title"] == "The Great Adventure"
    assert item["authors"] == "John Doe, Jane Smith"
    assert item["description"] == "A comprehensive guide to world building"
    # Cover should be extracted from first page
    assert item["cover_url"] is not None
    assert "/library/" in item["cover_url"]

    # Clean up
    delete_response = await client.delete(
        f"/libraries/{ontology_id}/items/{item_id}", headers=admin_headers
    )
    assert delete_response.status_code == 204


@pytest.mark.asyncio
async def test_library_item_manual_metadata_overrides_auto_extract(client):
    """Test that manual metadata takes precedence over auto-extraction."""
    admin_payload = {
        "username": "lib-admin-override",
        "password": "LibAdmin123",
        "full_name": "Library Admin Override",
        "email": "lib-admin-override@example.com",
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
        json={"name": "Override Library Ontology", "description": "Testing override"},
        headers=admin_headers,
    )
    assert ontology_response.status_code == 201, ontology_response.text
    ontology_id = ontology_response.json()["id"]

    # Create a PDF with metadata
    pdf_bytes = create_pdf_with_metadata(
        title="PDF Title",
        author="PDF Author",
        subject="PDF Subject",
    )
    pdf_buffer = BytesIO(pdf_bytes)

    # Create library item with auto_extract_metadata=True but also manual metadata
    create_response = await client.post(
        f"/libraries/{ontology_id}/items",
        headers=admin_headers,
        files={"file": ("content.pdf", pdf_buffer, "application/pdf")},
        data={
            "title": "Manual Title",
            "authors": "Manual Author",
            "description": "Manual Description",
            "auto_extract_metadata": "true",
        },
    )
    assert create_response.status_code == 201, create_response.text
    item = create_response.json()
    item_id = item["id"]

    # Verify manual metadata was used instead of extracted
    assert item["title"] == "Manual Title"
    assert item["authors"] == "Manual Author"
    assert item["description"] == "Manual Description"

    # Clean up
    delete_response = await client.delete(
        f"/libraries/{ontology_id}/items/{item_id}", headers=admin_headers
    )
    assert delete_response.status_code == 204


@pytest.mark.asyncio
async def test_library_item_with_authors_field(client):
    """Test creating and updating library items with authors field."""
    admin_payload = {
        "username": "lib-admin-authors",
        "password": "LibAdmin123",
        "full_name": "Library Admin Authors",
        "email": "lib-admin-authors@example.com",
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
        json={
            "name": "Authors Library Ontology",
            "description": "Testing authors field",
        },
        headers=admin_headers,
    )
    assert ontology_response.status_code == 201, ontology_response.text
    ontology_id = ontology_response.json()["id"]

    # Create library item with authors
    pdf_buffer = BytesIO(PDF_BYTES)
    create_response = await client.post(
        f"/libraries/{ontology_id}/items",
        headers=admin_headers,
        files={"file": ("content.pdf", pdf_buffer, "application/pdf")},
        data={
            "title": "Test Book",
            "authors": "Alice, Bob, Charlie",
            "description": "A test book",
        },
    )
    assert create_response.status_code == 201, create_response.text
    item = create_response.json()
    item_id = item["id"]

    assert item["title"] == "Test Book"
    assert item["authors"] == "Alice, Bob, Charlie"
    assert item["description"] == "A test book"

    # Update authors
    update_response = await client.put(
        f"/libraries/{ontology_id}/items/{item_id}",
        json={
            "authors": "David, Eve",
        },
        headers=admin_headers,
    )
    assert update_response.status_code == 200, update_response.text
    updated_item = update_response.json()
    assert updated_item["authors"] == "David, Eve"

    # Clean up
    delete_response = await client.delete(
        f"/libraries/{ontology_id}/items/{item_id}", headers=admin_headers
    )
    assert delete_response.status_code == 204
