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


@pytest.mark.asyncio
async def test_retrieved_chunk_schema_includes_book_metadata():
    """Test that RetrievedChunk schema includes book metadata fields."""
    from app.jobs.librarian.schemas import RetrievedChunk

    # Create a sample chunk with all fields
    chunk = RetrievedChunk(
        library_item_id=1,
        page_number=42,
        text="Sample text from a book",
        score=0.95,
        pdf_url="http://example.com/book.pdf",
        page_url="http://example.com/book.pdf#page=42",
        book_title="Test Book Title",
        book_authors="Test Author",
    )

    assert chunk.library_item_id == 1
    assert chunk.page_number == 42
    assert chunk.book_title == "Test Book Title"
    assert chunk.book_authors == "Test Author"
    assert chunk.pdf_url == "http://example.com/book.pdf"
    assert chunk.page_url == "http://example.com/book.pdf#page=42"

    # Test that book metadata can be None
    chunk_without_metadata = RetrievedChunk(
        library_item_id=2,
        page_number=10,
        text="Another sample",
        score=0.8,
    )

    assert chunk_without_metadata.book_title is None
    assert chunk_without_metadata.book_authors is None


@pytest.mark.asyncio
async def test_librarian_query_response_schema():
    """Test that LibrarianQueryResponse schema includes new fields."""
    from app.jobs.librarian.schemas import LibrarianQueryResponse, RetrievedChunk

    # Create a sample response with all new fields
    chunk1 = RetrievedChunk(
        library_item_id=1,
        page_number=10,
        text="Sample text",
        score=0.9,
        book_title="Book 1",
        book_authors="Author 1",
    )
    chunk2 = RetrievedChunk(
        library_item_id=2,
        page_number=20,
        text="More text",
        score=0.8,
        book_title="Book 2",
        book_authors="Author 2",
    )

    response = LibrarianQueryResponse(
        agent_id="test-agent-123",
        mode="both",
        query="Test query",
        subqueries=[],  # Empty in simplified version
        answer="Test answer with <sub library_item_id=\"1\" library_item_name=\"Book 1\" page=\"10\">",
        chunks=[chunk1, chunk2],
        sources_used=[chunk1],
        library_items_used=[1, 2],
    )

    assert response.agent_id == "test-agent-123"
    assert response.query == "Test query"
    assert len(response.subqueries) == 0  # Simplified version has no subqueries
    assert len(response.chunks) == 2
    assert len(response.sources_used) == 1
    assert response.sources_used[0].library_item_id == 1
    assert response.library_items_used == [1, 2]
    assert "<sub library_item_id=" in response.answer

    # Test with minimal fields
    minimal_response = LibrarianQueryResponse(
        agent_id="test-agent-456",
        mode="context",
        query="Another query",
    )

    assert minimal_response.subqueries == []
    assert minimal_response.sources_used == []
    assert minimal_response.chunks == []
    assert minimal_response.answer is None

