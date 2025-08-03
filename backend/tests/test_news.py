import pytest


@pytest.mark.asyncio
async def test_news_flow(async_client, create_user, login_and_get_token):
    await create_user(email="admin@test.com", password="pass", role="system admin")
    await create_user(email="user@test.com", password="pass", role="player")
    admin_token = await login_and_get_token("admin@test.com", "pass", "system admin")
    user_token = await login_and_get_token("user@test.com", "pass", "player")

    resp = await async_client.post(
        "/news/",
        json={"title": "Hello", "type": "feature", "description": "desc"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    news_id = resp.json()["id"]

    resp = await async_client.get(
        "/news/", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["seen"] is False
    assert data[0]["user_id"] is None

    resp = await async_client.post(
        f"/news/{news_id}/seen",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 200

    resp = await async_client.get(
        "/news/", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert resp.status_code == 200
    assert resp.json()[0]["seen"] is True
