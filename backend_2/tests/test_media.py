from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import get_settings
from app.models.user import UserRole


def _create_image() -> BytesIO:
    image = Image.new("RGB", (200, 200), color="green")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


@pytest.mark.asyncio
async def test_upload_model_image_and_validation(client):
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

    user_payload = {
        "username": "media-target",
        "password": "MediaTarget123",
        "full_name": "Media Target",
        "email": "media-target@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    user_response = await client.post("/users/", json=user_payload)
    assert user_response.status_code == 201, user_response.text
    user_id = str(user_response.json()["id"])

    buffer = _create_image()
    upload_response = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("image.png", buffer, "image/png")},
        data={"model": "user", "instance_id": user_id},
    )
    assert upload_response.status_code == 201, upload_response.text
    url = upload_response.json()["url"]
    settings = get_settings()
    base_url = (
        settings.media_public_url.rstrip("/")
        if settings.media_public_url
        else settings.media_base_url.rstrip("/")
    )
    assert url == f"{base_url}/user/{user_id}/image_url.png"

    media_root = Path(settings.media_root)
    image_path = media_root / "user" / user_id / "image_url.png"
    assert image_path.exists()

    # Unsupported model
    buffer = _create_image()
    unsupported = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("image.png", buffer, "image/png")},
        data={"model": "unknown", "instance_id": "1"},
    )
    assert unsupported.status_code == 400

    # Missing instance
    buffer = _create_image()
    missing = await client.post(
        "/media-admin/images",
        headers=admin_headers,
        files={"file": ("image.png", buffer, "image/png")},
        data={"model": "user", "instance_id": "9999"},
    )
    assert missing.status_code == 404

    # Cleanup file to avoid leaking artifacts in test runs
    if image_path.exists():
        image_path.unlink()
        try:
            image_path.parent.rmdir()
        except OSError:
            pass
