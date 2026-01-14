from __future__ import annotations

import pytest

from app.models.notification import NotificationType
from app.models.user import UserRole


@pytest.mark.asyncio
async def test_notification_crud_and_user_flow(client):
    admin_payload = {
        "username": "notify-admin",
        "password": "NotifyAdmin123",
        "full_name": "Notify Admin",
        "email": "notify-admin@example.com",
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
        "username": "notify-user",
        "password": "NotifyUser123",
        "full_name": "Notify User",
        "email": "notify-user@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    user_register = await client.post("/users/", json=user_payload)
    assert user_register.status_code == 201, user_register.text
    user_id = user_register.json()["id"]

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

    create_payload = {
        "notification_type": NotificationType.CONTENT_UPDATE.value,
        "title": "World updated",
        "description": "<p>New lore entries added.</p>",
        "author_type": "user",
        "author_id": str(admin_register.json()["id"]),
        "user_id": user_id,
        "send_email": True,
    }
    create_response = await client.post(
        "/notifications/", json=create_payload, headers=admin_headers
    )
    assert create_response.status_code == 201, create_response.text
    notification = create_response.json()
    notification_id = notification["id"]
    assert notification["read"] is False
    assert notification["user_id"] == user_id
    assert notification["send_email"] is True
    assert notification["sent_date"] is None

    list_admin = await client.get("/notifications/", headers=admin_headers)
    assert list_admin.status_code == 200
    assert any(item["id"] == notification_id for item in list_admin.json())

    list_user = await client.get("/notifications/me", headers=user_headers)
    assert list_user.status_code == 200
    assert len(list_user.json()) == 1

    unread_count = await client.get(
        "/notifications/me/unread-count", headers=user_headers
    )
    assert unread_count.status_code == 200
    assert unread_count.json()["unread_count"] == 1

    mark_read = await client.post(
        f"/notifications/{notification_id}/read",
        json={"read": True},
        headers=user_headers,
    )
    assert mark_read.status_code == 200
    assert mark_read.json()["read"] is True

    unread_after = await client.get(
        "/notifications/me/unread-count", headers=user_headers
    )
    assert unread_after.status_code == 200
    assert unread_after.json()["unread_count"] == 0

    update_response = await client.put(
        f"/notifications/{notification_id}",
        json={
            "title": "World updated again",
            "read": False,
            "send_email": False,
            "sent_date": "2025-01-03T00:00:00Z",
        },
        headers=admin_headers,
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["read"] is False
    assert update_response.json()["title"] == "World updated again"
    assert update_response.json()["send_email"] is False
    assert update_response.json()["sent_date"] == "2025-01-03T00:00:00+00:00"

    delete_response = await client.delete(
        f"/notifications/{notification_id}", headers=admin_headers
    )
    assert delete_response.status_code == 204, delete_response.text

    user_after_delete = await client.get("/notifications/me", headers=user_headers)
    assert user_after_delete.status_code == 200
    assert user_after_delete.json() == []

    forbidden_list = await client.get("/notifications/", headers=user_headers)
    assert forbidden_list.status_code == 403


@pytest.mark.asyncio
async def test_notification_preferences_toggle(client):
    admin_payload = {
        "username": "pref-admin",
        "password": "PrefAdmin123",
        "full_name": "Pref Admin",
        "email": "pref-admin@example.com",
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
        "username": "pref-user",
        "password": "PrefUser123",
        "full_name": "Pref User",
        "email": "pref-user@example.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    user_register = await client.post("/users/", json=user_payload)
    assert user_register.status_code == 201, user_register.text
    user_id = user_register.json()["id"]

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

    preferences_response = await client.get(
        "/notifications/me/preferences", headers=user_headers
    )
    assert preferences_response.status_code == 200, preferences_response.text
    preferences = {
        item["notification_type"]: item["enabled"]
        for item in preferences_response.json()
    }
    assert preferences[NotificationType.CONTENT_UPDATE.value] is True

    disable_response = await client.put(
        f"/notifications/me/preferences/{NotificationType.CONTENT_UPDATE.value}",
        json={"enabled": False},
        headers=user_headers,
    )
    assert disable_response.status_code == 200, disable_response.text
    assert disable_response.json()["enabled"] is False

    create_payload = {
        "notification_type": NotificationType.CONTENT_UPDATE.value,
        "title": "World updated",
        "description": "<p>New lore entries added.</p>",
        "author_type": "user",
        "author_id": str(admin_register.json()["id"]),
        "user_id": user_id,
        "send_email": True,
    }
    blocked_response = await client.post(
        "/notifications/", json=create_payload, headers=admin_headers
    )
    assert blocked_response.status_code == 409, blocked_response.text

    list_user = await client.get("/notifications/me", headers=user_headers)
    assert list_user.status_code == 200
    assert list_user.json() == []

    enable_response = await client.put(
        f"/notifications/me/preferences/{NotificationType.CONTENT_UPDATE.value}",
        json={"enabled": True},
        headers=user_headers,
    )
    assert enable_response.status_code == 200, enable_response.text
    assert enable_response.json()["enabled"] is True

    create_response = await client.post(
        "/notifications/", json=create_payload, headers=admin_headers
    )
    assert create_response.status_code == 201, create_response.text

    list_user_after = await client.get("/notifications/me", headers=user_headers)
    assert list_user_after.status_code == 200
    assert len(list_user_after.json()) == 1
