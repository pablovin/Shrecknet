"""Tests for Agent API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_available_jobs(client: AsyncClient, admin_token: str):
    """Test getting available job types."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = await client.get("/agents/jobs", headers=headers)
    
    assert response.status_code == 200
    jobs = response.json()
    assert isinstance(jobs, list)
    assert "elder" in jobs


@pytest.mark.asyncio
async def test_create_agent(client: AsyncClient, admin_token: str, ontology_id: int):
    """Test creating a new agent."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    agent_data = {
        "name": "Test Elder",
        "description": "A test elder agent",
        "writing_style": "Concise and wise",
        "job": "elder",
        "active": True,
        "ontology_ids": [ontology_id],
    }
    
    response = await client.post("/agents/", json=agent_data, headers=headers)
    
    assert response.status_code == 201
    agent = response.json()
    assert agent["name"] == "Test Elder"
    assert agent["job"] == "elder"
    assert agent["active"] is True
    assert ontology_id in agent["ontology_ids"]
    assert "id" in agent
    assert "created_at" in agent


@pytest.mark.asyncio
async def test_list_agents(client: AsyncClient, admin_token: str, ontology_id: int):
    """Test listing agents."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Create an agent first
    agent_data = {
        "name": "List Test Elder",
        "job": "elder",
        "ontology_ids": [ontology_id],
    }
    create_response = await client.post("/agents/", json=agent_data, headers=headers)
    assert create_response.status_code == 201
    
    # List agents
    response = await client.get("/agents/", headers=headers)
    
    assert response.status_code == 200
    agents = response.json()
    assert isinstance(agents, list)
    assert len(agents) > 0
    assert any(a["name"] == "List Test Elder" for a in agents)


@pytest.mark.asyncio
async def test_get_agent(client: AsyncClient, admin_token: str, ontology_id: int):
    """Test getting a specific agent."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Create an agent
    agent_data = {
        "name": "Get Test Elder",
        "job": "elder",
        "ontology_ids": [ontology_id],
    }
    create_response = await client.post("/agents/", json=agent_data, headers=headers)
    agent_id = create_response.json()["id"]
    
    # Get the agent
    response = await client.get(f"/agents/{agent_id}", headers=headers)
    
    assert response.status_code == 200
    agent = response.json()
    assert agent["id"] == agent_id
    assert agent["name"] == "Get Test Elder"


@pytest.mark.asyncio
async def test_update_agent(client: AsyncClient, admin_token: str, ontology_id: int):
    """Test updating an agent."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Create an agent
    agent_data = {
        "name": "Update Test Elder",
        "job": "elder",
        "active": True,
        "ontology_ids": [ontology_id],
    }
    create_response = await client.post("/agents/", json=agent_data, headers=headers)
    agent_id = create_response.json()["id"]
    
    # Update the agent
    update_data = {
        "name": "Updated Elder",
        "active": False,
    }
    response = await client.patch(f"/agents/{agent_id}", json=update_data, headers=headers)
    
    assert response.status_code == 200
    agent = response.json()
    assert agent["name"] == "Updated Elder"
    assert agent["active"] is False


@pytest.mark.asyncio
async def test_delete_agent(client: AsyncClient, admin_token: str, ontology_id: int):
    """Test deleting an agent."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Create an agent
    agent_data = {
        "name": "Delete Test Elder",
        "job": "elder",
        "ontology_ids": [ontology_id],
    }
    create_response = await client.post("/agents/", json=agent_data, headers=headers)
    agent_id = create_response.json()["id"]
    
    # Delete the agent
    response = await client.delete(f"/agents/{agent_id}", headers=headers)
    
    assert response.status_code == 204
    
    # Verify deletion
    get_response = await client.get(f"/agents/{agent_id}", headers=headers)
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_attach_detach_ontology(client: AsyncClient, admin_token: str, ontology_id: int):
    """Test attaching and detaching ontologies."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Create an agent without ontologies
    agent_data = {
        "name": "Ontology Test Elder",
        "job": "elder",
        "ontology_ids": [],
    }
    create_response = await client.post("/agents/", json=agent_data, headers=headers)
    agent_id = create_response.json()["id"]
    
    # Attach ontology
    attach_response = await client.post(
        f"/agents/{agent_id}/ontologies/{ontology_id}", headers=headers
    )
    assert attach_response.status_code == 200
    agent = attach_response.json()
    assert ontology_id in agent["ontology_ids"]
    
    # Detach ontology
    detach_response = await client.delete(
        f"/agents/{agent_id}/ontologies/{ontology_id}", headers=headers
    )
    assert detach_response.status_code == 200
    agent = detach_response.json()
    assert ontology_id not in agent["ontology_ids"]


@pytest.mark.asyncio
async def test_agent_requires_admin(client: AsyncClient, user_token: str):
    """Test that agent endpoints require admin role."""
    headers = {"Authorization": f"Bearer {user_token}"}
    
    # Try to list agents as non-admin
    response = await client.get("/agents/", headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_invalid_job_type(client: AsyncClient, admin_token: str):
    """Test that invalid job type is rejected."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    agent_data = {
        "name": "Invalid Job Elder",
        "job": "invalid_job",
        "ontology_ids": [],
    }
    
    response = await client.post("/agents/", json=agent_data, headers=headers)
    assert response.status_code == 400
    assert "invalid job type" in response.json()["detail"].lower()
