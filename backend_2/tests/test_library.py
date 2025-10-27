from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.models.user import UserRole

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"


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
