import pytest
from pathlib import Path

SYSTEM_ADMIN = {
    "nickname": "sysadmin",
    "email": "lib_admin@example.com",
    "password": "secret123",
    "role": "system admin",
    "image_url": "no image",
}
PLAYER = {
    "nickname": "player",
    "email": "lib_player@example.com",
    "password": "secret123",
    "role": "player",
    "image_url": "no image",
}

@pytest.mark.anyio
async def register_and_login(async_client, user_data):
    resp = await async_client.post("/user/", json=user_data)
    assert resp.status_code == 200, resp.text
    resp = await async_client.post(
        "/user/login",
        data={"username": user_data["email"], "password": user_data["password"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]

@pytest.mark.anyio
async def test_library_crud(async_client, tmp_path):
    admin_token = await register_and_login(async_client, SYSTEM_ADMIN)
    player_token = await register_and_login(async_client, PLAYER)

    file_path = tmp_path / "doc.pdf"
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(file_path))
    c.drawString(100, 750, "hello")
    c.showPage()
    c.save()

    with open(file_path, "rb") as f:
        resp = await async_client.post(
            "/library/",
            data={"name": "Doc", "system": "dnd", "description": "desc"},
            files={"file": ("doc.pdf", f, "application/pdf")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 200
    item_id = resp.json()["id"]
    stored = Path(resp.json()["path"])
    cover = Path(resp.json()["cover_url"])
    assert stored.is_file()
    assert cover.is_file()

    # player cannot create
    with open(file_path, "rb") as f:
        resp = await async_client.post(
            "/library/",
            data={"name": "P", "system": "dnd"},
            files={"file": ("p.txt", f, "text/plain")},
            headers={"Authorization": f"Bearer {player_token}"},
        )
    assert resp.status_code == 403

    # list
    resp = await async_client.get("/library/", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert any(it["id"] == item_id for it in resp.json())

    # download
    resp = await async_client.get(
        f"/library/{item_id}/download",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("application/pdf")

    # update
    resp = await async_client.patch(
        f"/library/{item_id}",
        json={"description": "new"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["description"] == "new"

    # delete
    resp = await async_client.delete(
        f"/library/{item_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert not stored.exists()
    assert not cover.parent.exists()
