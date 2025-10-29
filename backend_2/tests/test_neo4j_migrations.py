"""Tests for Neo4j database migrations."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.db.migrations import migrate_neo4j_embedding_properties


@pytest.fixture
def mock_neo4j_session():
    """Create a mock Neo4j session for testing."""
    return AsyncMock()


@pytest.mark.asyncio
async def test_migrate_neo4j_embedding_properties_no_nodes(mock_neo4j_session):
    """Test migration when no nodes need migration."""
    # Mock the check query to return 0 nodes
    check_result = AsyncMock()
    check_result.single = AsyncMock(return_value={"count": 0})
    mock_neo4j_session.run = AsyncMock(return_value=check_result)

    result = await migrate_neo4j_embedding_properties(mock_neo4j_session)

    assert result["nodes_migrated"] == 0
    assert result["status"] == "success"
    # Should only run the check query, not the update query
    assert mock_neo4j_session.run.call_count == 1


@pytest.mark.asyncio
async def test_migrate_neo4j_embedding_properties_with_nodes(mock_neo4j_session):
    """Test migration when nodes need migration."""
    # Mock the check query to return 10 nodes
    check_result = AsyncMock()
    check_result.single = AsyncMock(return_value={"count": 10})

    # Mock the update query to return 10 updated nodes
    update_result = AsyncMock()
    update_result.single = AsyncMock(return_value={"updated": 10})

    # Set up the session to return different results for each call
    mock_neo4j_session.run = AsyncMock(side_effect=[check_result, update_result])

    result = await migrate_neo4j_embedding_properties(mock_neo4j_session)

    assert result["nodes_migrated"] == 10
    assert result["status"] == "success"
    # Should run both the check and update queries
    assert mock_neo4j_session.run.call_count == 2


@pytest.mark.asyncio
async def test_migrate_neo4j_embedding_properties_idempotent(mock_neo4j_session):
    """Test that migration can be run multiple times without errors."""
    # First run: 5 nodes to migrate
    check_result_1 = AsyncMock()
    check_result_1.single = AsyncMock(return_value={"count": 5})
    update_result_1 = AsyncMock()
    update_result_1.single = AsyncMock(return_value={"updated": 5})

    # Second run: 0 nodes to migrate
    check_result_2 = AsyncMock()
    check_result_2.single = AsyncMock(return_value={"count": 0})

    mock_neo4j_session.run = AsyncMock(
        side_effect=[check_result_1, update_result_1, check_result_2]
    )

    # First migration
    result1 = await migrate_neo4j_embedding_properties(mock_neo4j_session)
    assert result1["nodes_migrated"] == 5
    assert result1["status"] == "success"

    # Second migration (idempotent)
    result2 = await migrate_neo4j_embedding_properties(mock_neo4j_session)
    assert result2["nodes_migrated"] == 0
    assert result2["status"] == "success"


@pytest.mark.asyncio
async def test_migrate_neo4j_embedding_properties_check_query():
    """Test that the check query is correctly formatted."""
    mock_session = AsyncMock()
    check_result = AsyncMock()
    check_result.single = AsyncMock(return_value={"count": 0})
    mock_session.run = AsyncMock(return_value=check_result)

    await migrate_neo4j_embedding_properties(mock_session)

    # Verify the check query was called with correct parameters
    call_args = mock_session.run.call_args_list[0]
    query = call_args[0][0]
    assert "MATCH (n:EntityInstance)" in query
    assert "n.is_embedded IS NULL" in query
    assert "count(n)" in query


@pytest.mark.asyncio
async def test_migrate_neo4j_embedding_properties_update_query():
    """Test that the update query is correctly formatted."""
    mock_session = AsyncMock()

    # Mock check to return nodes needing migration
    check_result = AsyncMock()
    check_result.single = AsyncMock(return_value={"count": 5})

    # Mock update result
    update_result = AsyncMock()
    update_result.single = AsyncMock(return_value={"updated": 5})

    mock_session.run = AsyncMock(side_effect=[check_result, update_result])

    await migrate_neo4j_embedding_properties(mock_session)

    # Verify the update query was called with correct parameters
    call_args = mock_session.run.call_args_list[1]
    query = call_args[0][0]
    assert "MATCH (n:EntityInstance)" in query
    assert "n.is_embedded IS NULL" in query
    assert "SET n.is_embedded = false" in query
    assert "n.last_embedded_date = null" in query
    assert "count(n)" in query
