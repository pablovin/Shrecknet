"""Tests for Librarian job functionality."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_librarian_in_available_jobs(client: AsyncClient, admin_token: str):
    """Test that librarian is in available job types."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = await client.get("/agents/jobs", headers=headers)

    assert response.status_code == 200
    jobs = response.json()
    assert isinstance(jobs, list)
    assert "librarian" in jobs
    assert "elder" in jobs


@pytest.mark.asyncio
async def test_create_librarian_agent(
    client: AsyncClient, admin_token: str, ontology_id: int
):
    """Test creating a librarian agent."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    agent_data = {
        "name": "Test Librarian",
        "description": "A test librarian agent for PDF books",
        "writing_style": "Clear and precise, citing page numbers",
        "job": "librarian",
        "active": True,
        "ontology_ids": [ontology_id],
    }

    response = await client.post("/agents/", json=agent_data, headers=headers)

    assert response.status_code == 201
    agent = response.json()
    assert agent["name"] == "Test Librarian"
    assert agent["job"] == "librarian"
    assert agent["active"] is True
    assert ontology_id in agent["ontology_ids"]
    assert "id" in agent


@pytest.mark.asyncio
async def test_list_librarian_agents(
    client: AsyncClient, admin_token: str, ontology_id: int
):
    """Test filtering agents by librarian job type."""
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Create a librarian agent
    agent_data = {
        "name": "Librarian for List Test",
        "job": "librarian",
        "ontology_ids": [ontology_id],
    }
    create_response = await client.post("/agents/", json=agent_data, headers=headers)
    assert create_response.status_code == 201

    # List only librarian agents
    response = await client.get("/agents/?job=librarian", headers=headers)

    assert response.status_code == 200
    agents = response.json()
    assert isinstance(agents, list)
    assert len(agents) > 0
    assert all(agent["job"] == "librarian" for agent in agents)
    assert any(agent["name"] == "Librarian for List Test" for agent in agents)


@pytest.mark.asyncio
async def test_update_agent_job_type(
    client: AsyncClient, admin_token: str, ontology_id: int
):
    """Test that invalid job type is rejected."""
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Create an agent
    agent_data = {
        "name": "Update Test Agent",
        "job": "elder",
        "ontology_ids": [ontology_id],
    }
    create_response = await client.post("/agents/", json=agent_data, headers=headers)
    agent_id = create_response.json()["id"]

    # Try to update to invalid job type
    update_data = {
        "job": "invalid_job_type",
    }
    response = await client.patch(
        f"/agents/{agent_id}", json=update_data, headers=headers
    )

    # Should reject invalid job type
    assert response.status_code == 400
    assert "invalid" in response.json()["detail"].lower()
