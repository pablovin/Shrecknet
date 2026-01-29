"""
Test to verify the Neo4j driver session pattern works correctly with run_async.

This test ensures that using driver.session() directly (as in _embed_ontology_impl)
avoids the "Future attached to a different loop" error when called from async contexts.
"""

from __future__ import annotations

import asyncio

import pytest

from app.utils.async_helpers import run_async


class MockAsyncSession:
    """Mock async session to simulate Neo4j session behavior."""

    async def __aenter__(self):
        await asyncio.sleep(0.001)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await asyncio.sleep(0.001)

    async def run(self, query: str, **kwargs):
        """Mock query execution."""
        await asyncio.sleep(0.001)
        return MockResult()


class MockResult:
    """Mock result to simulate Neo4j query result."""

    async def single(self):
        await asyncio.sleep(0.001)
        return {"count": 5}


class MockDriver:
    """Mock driver to simulate Neo4j driver behavior."""

    def session(self, database: str = "neo4j"):
        return MockAsyncSession()


async def mock_embed_impl_with_driver_session(job_id: int, ontology_id: int):
    """
    Mock implementation using driver.session() pattern.

    This simulates the pattern used in _embed_ontology_impl after the fix.
    """
    driver = MockDriver()
    async with driver.session(database="neo4j") as session:
        # Simulate a query
        result = await session.run(
            "MATCH (n:EntityInstance) WHERE n.ontology_id = $ontology_id RETURN count(n)",
            ontology_id=ontology_id,
        )
        record = await result.single()
        count = record["count"]

        return {
            "nodes_processed": count,
            "nodes_failed": 0,
            "status": "success",
        }


@pytest.mark.asyncio
async def test_driver_session_pattern_from_async_context():
    """
    Test that driver.session() pattern works when called via run_async from async context.

    This simulates the scenario where:
    1. FastAPI endpoint is async (running in event loop)
    2. task_always_eager=True, so Celery task runs synchronously
    3. Celery task calls run_async() which creates new thread + event loop
    4. Inside run_async, we use driver.session() (not async generator)

    This should NOT raise "Future attached to a different loop" error.
    """
    # This test runs in an async context (like FastAPI endpoint)
    # Simulate calling the task implementation via run_async
    result = run_async(mock_embed_impl_with_driver_session(123, 1))

    assert result["status"] == "success"
    assert result["nodes_processed"] == 5
    assert result["nodes_failed"] == 0


@pytest.mark.asyncio
async def test_multiple_driver_sessions_from_async_context():
    """
    Test that multiple driver.session() calls work correctly.

    This ensures that the pattern works even when called multiple times,
    simulating multiple task executions.
    """
    # Simulate multiple task executions
    for i in range(3):
        result = run_async(mock_embed_impl_with_driver_session(i, i))
        assert result["status"] == "success"


def test_driver_session_pattern_from_sync_context():
    """
    Test that driver.session() pattern also works from sync context.

    This ensures backward compatibility when Celery runs tasks in actual
    background workers (celery_task_always_eager=False).
    """

    async def impl():
        return await mock_embed_impl_with_driver_session(123, 1)

    result = run_async(impl())
    assert result["status"] == "success"
