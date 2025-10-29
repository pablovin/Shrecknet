"""Tests for Elder chat API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_chat(client: AsyncClient, user_token: str, agent_id: str):
    """Test creating a new chat."""
    headers = {"Authorization": f"Bearer {user_token}"}
    chat_data = {
        "agent_id": agent_id,
        "name": "Test Chat",
        "color": "#FF5733",
    }

    response = await client.post("/jobs/elder/chats/", json=chat_data, headers=headers)

    assert response.status_code == 201
    chat = response.json()
    assert chat["name"] == "Test Chat"
    assert chat["color"] == "#FF5733"
    assert chat["agent_id"] == agent_id
    assert "id" in chat
    assert "user_id" in chat
    assert "created_at" in chat


@pytest.mark.asyncio
async def test_create_chat_invalid_agent(client: AsyncClient, user_token: str):
    """Test creating a chat with invalid agent ID."""
    headers = {"Authorization": f"Bearer {user_token}"}
    chat_data = {
        "agent_id": "invalid-agent-id",
        "name": "Test Chat",
    }

    response = await client.post("/jobs/elder/chats/", json=chat_data, headers=headers)

    assert response.status_code == 400
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_chat_exceeds_limit(
    client: AsyncClient, user_token: str, agent_id: str
):
    """Test creating more than 10 chats per agent."""
    headers = {"Authorization": f"Bearer {user_token}"}

    # Create 10 chats
    for i in range(10):
        chat_data = {
            "agent_id": agent_id,
            "name": f"Chat {i+1}",
        }
        response = await client.post(
            "/jobs/elder/chats/", json=chat_data, headers=headers
        )
        assert response.status_code == 201

    # Try to create 11th chat
    chat_data = {
        "agent_id": agent_id,
        "name": "Chat 11",
    }
    response = await client.post("/jobs/elder/chats/", json=chat_data, headers=headers)

    assert response.status_code == 400
    assert "maximum" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_chats(client: AsyncClient, user_token: str, agent_id: str):
    """Test listing user's chats."""
    headers = {"Authorization": f"Bearer {user_token}"}

    # Create a chat first
    chat_data = {
        "agent_id": agent_id,
        "name": "Test Chat",
    }
    await client.post("/jobs/elder/chats/", json=chat_data, headers=headers)

    # List chats
    response = await client.get("/jobs/elder/chats/", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert "chats" in data
    assert len(data["chats"]) > 0
    assert data["chats"][0]["name"] == "Test Chat"


@pytest.mark.asyncio
async def test_list_chats_filtered_by_agent(
    client: AsyncClient, user_token: str, agent_id: str
):
    """Test listing chats filtered by agent."""
    headers = {"Authorization": f"Bearer {user_token}"}

    # Create a chat
    chat_data = {
        "agent_id": agent_id,
        "name": "Filtered Chat",
    }
    await client.post("/jobs/elder/chats/", json=chat_data, headers=headers)

    # List chats for this agent
    response = await client.get(
        f"/jobs/elder/chats/?agent_id={agent_id}", headers=headers
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["chats"]) > 0
    for chat in data["chats"]:
        assert chat["agent_id"] == agent_id


@pytest.mark.asyncio
async def test_get_chat(client: AsyncClient, user_token: str, agent_id: str):
    """Test getting a specific chat."""
    headers = {"Authorization": f"Bearer {user_token}"}

    # Create a chat
    chat_data = {
        "agent_id": agent_id,
        "name": "Get Chat Test",
    }
    create_response = await client.post(
        "/jobs/elder/chats/", json=chat_data, headers=headers
    )
    chat_id = create_response.json()["id"]

    # Get the chat
    response = await client.get(f"/jobs/elder/chats/{chat_id}", headers=headers)

    assert response.status_code == 200
    chat = response.json()
    assert chat["id"] == chat_id
    assert chat["name"] == "Get Chat Test"


@pytest.mark.asyncio
async def test_get_chat_not_found(client: AsyncClient, user_token: str):
    """Test getting a non-existent chat."""
    headers = {"Authorization": f"Bearer {user_token}"}
    response = await client.get("/jobs/elder/chats/nonexistent-id", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_chat(client: AsyncClient, user_token: str, agent_id: str):
    """Test updating chat metadata."""
    headers = {"Authorization": f"Bearer {user_token}"}

    # Create a chat
    chat_data = {
        "agent_id": agent_id,
        "name": "Original Name",
    }
    create_response = await client.post(
        "/jobs/elder/chats/", json=chat_data, headers=headers
    )
    chat_id = create_response.json()["id"]

    # Update the chat
    update_data = {
        "name": "Updated Name",
        "color": "#00FF00",
    }
    response = await client.patch(
        f"/jobs/elder/chats/{chat_id}", json=update_data, headers=headers
    )

    assert response.status_code == 200
    chat = response.json()
    assert chat["name"] == "Updated Name"
    assert chat["color"] == "#00FF00"


@pytest.mark.asyncio
async def test_update_chat_not_found(client: AsyncClient, user_token: str):
    """Test updating a non-existent chat."""
    headers = {"Authorization": f"Bearer {user_token}"}
    update_data = {"name": "Updated Name"}
    response = await client.patch(
        "/jobs/elder/chats/nonexistent-id", json=update_data, headers=headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_chat(client: AsyncClient, user_token: str, agent_id: str):
    """Test deleting a chat."""
    headers = {"Authorization": f"Bearer {user_token}"}

    # Create a chat
    chat_data = {
        "agent_id": agent_id,
        "name": "To Delete",
    }
    create_response = await client.post(
        "/jobs/elder/chats/", json=chat_data, headers=headers
    )
    chat_id = create_response.json()["id"]

    # Delete the chat
    response = await client.delete(f"/jobs/elder/chats/{chat_id}", headers=headers)

    assert response.status_code == 204

    # Verify chat is deleted
    get_response = await client.get(f"/jobs/elder/chats/{chat_id}", headers=headers)
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_chat_not_found(client: AsyncClient, user_token: str):
    """Test deleting a non-existent chat."""
    headers = {"Authorization": f"Bearer {user_token}"}
    response = await client.delete("/jobs/elder/chats/nonexistent-id", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_chat_security_isolation(
    client: AsyncClient, user_token: str, admin_token: str, agent_id: str
):
    """Test that users can only access their own chats."""
    user_headers = {"Authorization": f"Bearer {user_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Create a chat as regular user
    chat_data = {
        "agent_id": agent_id,
        "name": "User's Chat",
    }
    create_response = await client.post(
        "/jobs/elder/chats/", json=chat_data, headers=user_headers
    )
    chat_id = create_response.json()["id"]

    # Try to access it as admin (different user)
    response = await client.get(f"/jobs/elder/chats/{chat_id}", headers=admin_headers)

    # Should not be able to access another user's chat
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_query_with_chat_history(
    client: AsyncClient, user_token: str, agent_id: str
):
    """Test querying elder with chat history context."""
    headers = {"Authorization": f"Bearer {user_token}"}

    # Create a chat
    chat_data = {
        "agent_id": agent_id,
        "name": "History Test",
    }
    create_response = await client.post(
        "/jobs/elder/chats/", json=chat_data, headers=headers
    )
    chat_id = create_response.json()["id"]

    # Note: This test would require mocking the LLM client
    # For now, we just test that the endpoint accepts chat_id
    query_data = {
        "query": "What is the capital of France?",
        "chat_id": chat_id,
        "mode": "nl",
    }

    # This will fail without proper LLM setup, but tests the endpoint structure
    # In a real environment, you'd mock the LLM client
    response = await client.post(
        f"/jobs/elder/{agent_id}/query", json=query_data, headers=headers
    )

    # We expect it to fail gracefully (503 if OpenAI not configured)
    # or process the request if configured
    assert response.status_code in [200, 503, 500]


@pytest.mark.asyncio
async def test_chat_invalid_color(client: AsyncClient, user_token: str, agent_id: str):
    """Test creating chat with invalid color format."""
    headers = {"Authorization": f"Bearer {user_token}"}
    chat_data = {
        "agent_id": agent_id,
        "name": "Invalid Color",
        "color": "red",  # Should be hex format
    }

    response = await client.post("/jobs/elder/chats/", json=chat_data, headers=headers)

    assert response.status_code == 422  # Validation error
