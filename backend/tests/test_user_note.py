import pytest

USER1 = {
    "nickname": "noteuser1",
    "email": "noteuser1@example.com",
    "password": "secret",
    "role": "writer",
    "image_url": "no image",
}

USER2 = {
    "nickname": "noteuser2",
    "email": "noteuser2@example.com",
    "password": "secret",
    "role": "writer",
    "image_url": "no image",
}

@pytest.mark.anyio
async def test_user_notes_crud_and_sharing(async_client, create_user, login_and_get_token):
    user1 = await create_user(**USER1)
    user2 = await create_user(**USER2)

    token1 = await login_and_get_token(USER1["email"], USER1["password"], USER1["role"])
    token2 = await login_and_get_token(USER2["email"], USER2["password"], USER2["role"])

    note_payload = {"title": "My Note", "content": "Hello", "tags": ["a", "b"]}
    resp = await async_client.post("/user_notes/", json=note_payload, headers={"Authorization": f"Bearer {token1}"})
    assert resp.status_code == 200, resp.text
    note_id = resp.json()["id"]

    resp = await async_client.get(f"/user_notes/{note_id}", headers={"Authorization": f"Bearer {token1}"})
    assert resp.status_code == 200

    resp = await async_client.get("/user_notes/", headers={"Authorization": f"Bearer {token1}"})
    assert any(n["id"] == note_id for n in resp.json())

    resp = await async_client.get(f"/user_notes/{note_id}", headers={"Authorization": f"Bearer {token2}"})
    assert resp.status_code == 404

    resp = await async_client.patch(
        f"/user_notes/{note_id}",
        json={"shared_with_user_ids": [user2["id"]]},
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert resp.status_code == 200

    resp = await async_client.get(f"/user_notes/{note_id}", headers={"Authorization": f"Bearer {token2}"})
    assert resp.status_code == 200

    resp = await async_client.patch(
        f"/user_notes/{note_id}",
        json={"title": "fail"},
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert resp.status_code == 404

    resp = await async_client.get("/user_notes/", params={"search": "Hello"}, headers={"Authorization": f"Bearer {token1}"})
    assert any(n["id"] == note_id for n in resp.json())

    resp = await async_client.delete(f"/user_notes/{note_id}", headers={"Authorization": f"Bearer {token1}"})
    assert resp.status_code == 200
    resp = await async_client.get(f"/user_notes/{note_id}", headers={"Authorization": f"Bearer {token1}"})
    assert resp.status_code == 404
