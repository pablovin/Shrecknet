"""Tests for the GraphMaintenanceService utilities."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.graph_service import GraphMaintenanceService


@pytest.mark.asyncio
async def test_clear_graph_deletes_and_counts() -> None:
    """GraphMaintenanceService.clear_graph should delete everything and report counts."""
    session = AsyncMock()
    node_result = AsyncMock()
    node_result.single = AsyncMock(return_value={"count": 5})
    rel_result = AsyncMock()
    rel_result.single = AsyncMock(return_value={"count": 8})
    delete_result = AsyncMock()

    session.run.side_effect = [node_result, rel_result, delete_result]

    service = GraphMaintenanceService(session)
    result = await service.clear_graph()

    assert result == {"nodes_deleted": 5, "relationships_deleted": 8}
    assert session.run.call_count == 3
    session.run.assert_any_call("MATCH (n) RETURN count(n) AS count")
    session.run.assert_any_call("MATCH ()-[r]-() RETURN count(r) AS count")
    session.run.assert_called_with("MATCH (n) DETACH DELETE n")
