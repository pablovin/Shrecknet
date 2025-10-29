from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import get_settings
from app.models.user import UserRole


def _create_image() -> BytesIO:
    """Create a test image."""
    image = Image.new("RGB", (200, 200), color="green")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


@pytest.mark.asyncio
async def test_upload_main_image(client):
    """Test uploading a main image (is_main=True)."""
    # Create an admin user
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

    # Get admin token
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

    settings = get_settings()
    base_url = (
        settings.media_public_url.rstrip("/")
        if settings.media_public_url
        else settings.media_base_url.rstrip("/")
    )
    media_root = Path(settings.media_root)

    # Upload main image
    buffer = _create_image()
    upload_response = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("image.png", buffer, "image/png")},
        data={"content_type": "user", "content_id": "123", "is_main": "true"},
    )
    assert upload_response.status_code == 201, upload_response.text
    url = upload_response.json()["url"]
    assert url == f"{base_url}/user/123/file.png"

    # Verify file exists
    image_path = media_root / "user" / "123" / "file.png"
    assert image_path.exists()

    # Upload another main image - should overwrite
    buffer = _create_image()
    upload_response2 = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("image2.png", buffer, "image/png")},
        data={"content_type": "user", "content_id": "123", "is_main": "true"},
    )
    assert upload_response2.status_code == 201, upload_response2.text
    url2 = upload_response2.json()["url"]
    assert url2 == f"{base_url}/user/123/file.png"

    # File should still exist (overwritten)
    assert image_path.exists()

    # Should only have one file.png
    files = list((media_root / "user" / "123").glob("*.png"))
    assert len(files) == 1
    assert files[0].name == "file.png"

    # Cleanup
    if image_path.exists():
        image_path.unlink()
    try:
        image_path.parent.rmdir()
        image_path.parent.parent.rmdir()
    except OSError:
        pass


@pytest.mark.asyncio
async def test_upload_non_main_images(client):
    """Test uploading non-main images (is_main=False)."""
    # Create an admin user
    admin_payload = {
        "username": "media-admin2",
        "password": "MediaAdmin123",
        "full_name": "Media Admin",
        "email": "media-admin2@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    admin_register = await client.post("/users/", json=admin_payload)
    assert admin_register.status_code == 201, admin_register.text

    # Get admin token
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

    settings = get_settings()
    base_url = (
        settings.media_public_url.rstrip("/")
        if settings.media_public_url
        else settings.media_base_url.rstrip("/")
    )
    media_root = Path(settings.media_root)

    # Upload first non-main image
    buffer = _create_image()
    upload_response1 = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("image.png", buffer, "image/png")},
        data={"content_type": "gallery", "content_id": "456", "is_main": "false"},
    )
    assert upload_response1.status_code == 201, upload_response1.text
    url1 = upload_response1.json()["url"]
    assert url1 == f"{base_url}/gallery/456/1.png"

    # Verify file exists
    image_path1 = media_root / "gallery" / "456" / "1.png"
    assert image_path1.exists()

    # Upload second non-main image
    buffer = _create_image()
    upload_response2 = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("image2.png", buffer, "image/png")},
        data={"content_type": "gallery", "content_id": "456", "is_main": "false"},
    )
    assert upload_response2.status_code == 201, upload_response2.text
    url2 = upload_response2.json()["url"]
    assert url2 == f"{base_url}/gallery/456/2.png"

    # Verify file exists
    image_path2 = media_root / "gallery" / "456" / "2.png"
    assert image_path2.exists()

    # Upload third non-main image
    buffer = _create_image()
    upload_response3 = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("image3.png", buffer, "image/png")},
        data={"content_type": "gallery", "content_id": "456", "is_main": "false"},
    )
    assert upload_response3.status_code == 201, upload_response3.text
    url3 = upload_response3.json()["url"]
    assert url3 == f"{base_url}/gallery/456/3.png"

    # Verify file exists
    image_path3 = media_root / "gallery" / "456" / "3.png"
    assert image_path3.exists()

    # Should have three files
    files = list((media_root / "gallery" / "456").glob("*.png"))
    assert len(files) == 3
    file_names = sorted([f.name for f in files])
    assert file_names == ["1.png", "2.png", "3.png"]

    # Cleanup
    for path in [image_path1, image_path2, image_path3]:
        if path.exists():
            path.unlink()
    try:
        image_path1.parent.rmdir()
        image_path1.parent.parent.rmdir()
    except OSError:
        pass


@pytest.mark.asyncio
async def test_upload_mixed_main_and_non_main(client):
    """Test uploading both main and non-main images together."""
    # Create an admin user
    admin_payload = {
        "username": "media-admin3",
        "password": "MediaAdmin123",
        "full_name": "Media Admin",
        "email": "media-admin3@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    admin_register = await client.post("/users/", json=admin_payload)
    assert admin_register.status_code == 201, admin_register.text

    # Get admin token
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

    settings = get_settings()
    base_url = (
        settings.media_public_url.rstrip("/")
        if settings.media_public_url
        else settings.media_base_url.rstrip("/")
    )
    media_root = Path(settings.media_root)

    # Upload main image
    buffer = _create_image()
    upload_main = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("main.png", buffer, "image/png")},
        data={"content_type": "post", "content_id": "789", "is_main": "true"},
    )
    assert upload_main.status_code == 201, upload_main.text
    assert upload_main.json()["url"] == f"{base_url}/post/789/file.png"

    # Upload non-main images
    buffer = _create_image()
    upload1 = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("image1.png", buffer, "image/png")},
        data={"content_type": "post", "content_id": "789", "is_main": "false"},
    )
    assert upload1.status_code == 201, upload1.text
    assert upload1.json()["url"] == f"{base_url}/post/789/1.png"

    buffer = _create_image()
    upload2 = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("image2.png", buffer, "image/png")},
        data={"content_type": "post", "content_id": "789", "is_main": "false"},
    )
    assert upload2.status_code == 201, upload2.text
    assert upload2.json()["url"] == f"{base_url}/post/789/2.png"

    # Should have three files total: file.png, 1.png, 2.png
    files = list((media_root / "post" / "789").glob("*.png"))
    assert len(files) == 3
    file_names = sorted([f.name for f in files])
    assert file_names == ["1.png", "2.png", "file.png"]

    # Cleanup
    for f in files:
        if f.exists():
            f.unlink()
    try:
        (media_root / "post" / "789").rmdir()
        (media_root / "post").rmdir()
    except OSError:
        pass


@pytest.mark.asyncio
async def test_invalid_content_type(client):
    """Test that invalid content_type is rejected."""
    # Create an admin user
    admin_payload = {
        "username": "media-admin4",
        "password": "MediaAdmin123",
        "full_name": "Media Admin",
        "email": "media-admin4@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    admin_register = await client.post("/users/", json=admin_payload)
    assert admin_register.status_code == 201, admin_register.text

    # Get admin token
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

    # Try with invalid content_type (contains path traversal)
    buffer = _create_image()
    upload_response = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("image.png", buffer, "image/png")},
        data={"content_type": "../etc", "content_id": "123", "is_main": "false"},
    )
    assert upload_response.status_code == 400
    assert "Invalid content_type" in upload_response.json()["detail"]


@pytest.mark.asyncio
async def test_requires_authentication(client):
    """Test that the endpoint requires authentication."""
    buffer = _create_image()
    upload_response = await client.post(
        "/media-admin/images",
        files={"file": ("image.png", buffer, "image/png")},
        data={"content_type": "user", "content_id": "123", "is_main": "true"},
    )
    assert upload_response.status_code == 401


@pytest.mark.asyncio
async def test_requires_admin_or_world_builder_role(client):
    """Test that the endpoint requires ADMIN or WORLD_BUILDER role."""
    # First create an admin user (so the player isn't auto-promoted)
    admin_payload = {
        "username": "first-admin",
        "password": "FirstAdmin123",
        "full_name": "First Admin",
        "email": "first-admin@example.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    await client.post("/users/", json=admin_payload)

    # Create a player user
    player_payload = {
        "username": "player-user",
        "password": "PlayerUser123",
        "full_name": "Player User",
        "email": "player@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    player_register = await client.post("/users/", json=player_payload)
    assert player_register.status_code == 201

    # Get player token
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

    # Try to upload with player role
    buffer = _create_image()
    upload_response = await client.post(
        "/media-admin/images",
        headers=player_headers,
        files={"file": ("image.png", buffer, "image/png")},
        data={"content_type": "user", "content_id": "123", "is_main": "true"},
    )
    assert upload_response.status_code == 403
