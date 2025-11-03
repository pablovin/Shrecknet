import pytest

ADMIN_USER = {
    "nickname": "admin",
    "email": "admin@example.com",
    "password": "admin_password",
    "role": "system admin",
    "image_url": "no image",
}

AUTHOR_USER = {
    "nickname": "author",
    "email": "author@example.com",
    "password": "author_password",
    "role": "writer",
    "image_url": "no image",
}

SHARED_USER1 = {
    "nickname": "shared1",
    "email": "shared1@example.com",
    "password": "shared_password",
    "role": "writer",
    "image_url": "no image",
}

SHARED_USER2 = {
    "nickname": "shared2",
    "email": "shared2@example.com",
    "password": "shared_password",
    "role": "writer",
    "image_url": "no image",
}


@pytest.mark.anyio
async def test_admin_create_note_for_user(
    async_client, create_user, login_and_get_token
):
    """Test that admin can create a note on behalf of another user"""
    admin = await create_user(**ADMIN_USER)
    author = await create_user(**AUTHOR_USER)
    
    admin_token = await login_and_get_token(ADMIN_USER["email"], ADMIN_USER["password"], ADMIN_USER["role"])
    author_token = await login_and_get_token(AUTHOR_USER["email"], AUTHOR_USER["password"], AUTHOR_USER["role"])
    
    # Admin creates a note for the author
    note_payload = {
        "title": "Admin Created Note",
        "content": "This note was created by admin for author",
        "tags": ["admin", "test"],
        "author_user_id": author["id"],
        "shared_with_user_ids": []
    }
    
    resp = await async_client.post(
        "/admin/user_notes/",
        json=note_payload,
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200, resp.text
    note_data = resp.json()
    assert note_data["user_id"] == author["id"]
    assert note_data["title"] == "Admin Created Note"
    note_id = note_data["id"]
    
    # Author should be able to see the note
    resp = await async_client.get(
        f"/user_notes/{note_id}",
        headers={"Authorization": f"Bearer {author_token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == note_id


@pytest.mark.anyio
async def test_admin_create_shared_note(
    async_client, create_user, login_and_get_token
):
    """Test that admin can create a note shared with multiple users"""
    admin = await create_user(**ADMIN_USER)
    author = await create_user(**AUTHOR_USER)
    shared1 = await create_user(**SHARED_USER1)
    shared2 = await create_user(**SHARED_USER2)
    
    admin_token = await login_and_get_token(ADMIN_USER["email"], ADMIN_USER["password"], ADMIN_USER["role"])
    author_token = await login_and_get_token(AUTHOR_USER["email"], AUTHOR_USER["password"], AUTHOR_USER["role"])
    shared1_token = await login_and_get_token(SHARED_USER1["email"], SHARED_USER1["password"], SHARED_USER1["role"])
    shared2_token = await login_and_get_token(SHARED_USER2["email"], SHARED_USER2["password"], SHARED_USER2["role"])
    
    # Admin creates a note shared with multiple users
    note_payload = {
        "title": "Shared Meeting Notes",
        "content": "Meeting notes for the team",
        "tags": ["meeting", "team"],
        "author_user_id": author["id"],
        "shared_with_user_ids": [shared1["id"], shared2["id"]]
    }
    
    resp = await async_client.post(
        "/admin/user_notes/",
        json=note_payload,
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200, resp.text
    note_data = resp.json()
    assert note_data["user_id"] == author["id"]
    assert set(note_data["shared_with_user_ids"]) == {shared1["id"], shared2["id"]}
    note_id = note_data["id"]
    
    # All users should be able to see the note
    for token in [author_token, shared1_token, shared2_token]:
        resp = await async_client.get(
            f"/user_notes/{note_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == note_id
    
    # Check that shared users see it in their list
    resp = await async_client.get(
        "/user_notes/",
        headers={"Authorization": f"Bearer {shared1_token}"}
    )
    assert resp.status_code == 200
    note_ids = [n["id"] for n in resp.json()]
    assert note_id in note_ids


@pytest.mark.anyio
async def test_non_admin_cannot_create_admin_note(
    async_client, create_user, login_and_get_token
):
    """Test that non-admin users cannot use the admin endpoint"""
    author = await create_user(**AUTHOR_USER)
    other_user = await create_user(**SHARED_USER1)
    
    author_token = await login_and_get_token(AUTHOR_USER["email"], AUTHOR_USER["password"], AUTHOR_USER["role"])
    
    # Non-admin tries to create a note for another user
    note_payload = {
        "title": "Unauthorized Note",
        "content": "This should fail",
        "author_user_id": other_user["id"],
        "shared_with_user_ids": []
    }
    
    resp = await async_client.post(
        "/admin/user_notes/",
        json=note_payload,
        headers={"Authorization": f"Bearer {author_token}"}
    )
    assert resp.status_code == 403  # Forbidden


@pytest.mark.anyio
async def test_admin_create_note_invalid_author(
    async_client, create_user, login_and_get_token
):
    """Test that admin cannot create note for non-existent user"""
    admin = await create_user(**ADMIN_USER)
    admin_token = await login_and_get_token(ADMIN_USER["email"], ADMIN_USER["password"], ADMIN_USER["role"])
    
    # Try to create note with invalid author_user_id
    note_payload = {
        "title": "Invalid Author Note",
        "content": "This should fail",
        "author_user_id": 99999,  # Non-existent user
        "shared_with_user_ids": []
    }
    
    resp = await async_client.post(
        "/admin/user_notes/",
        json=note_payload,
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_admin_create_note_invalid_shared_user(
    async_client, create_user, login_and_get_token
):
    """Test that admin cannot create note with non-existent shared user"""
    admin = await create_user(**ADMIN_USER)
    author = await create_user(**AUTHOR_USER)
    admin_token = await login_and_get_token(ADMIN_USER["email"], ADMIN_USER["password"], ADMIN_USER["role"])
    
    # Try to create note with invalid shared_with_user_ids
    note_payload = {
        "title": "Invalid Shared User Note",
        "content": "This should fail",
        "author_user_id": author["id"],
        "shared_with_user_ids": [99999]  # Non-existent user
    }
    
    resp = await async_client.post(
        "/admin/user_notes/",
        json=note_payload,
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 404
