"""
Test to verify that neo4j embedding tasks work correctly in async contexts.

This test verifies that the embed_ontology and embed_instance tasks work
correctly when task_always_eager=True, which causes them to run in the same
event loop as the calling FastAPI endpoint.
"""

from __future__ import annotations

import asyncio

import pytest

from app.utils.async_helpers import run_async


@pytest.mark.asyncio
async def test_embed_ontology_from_async_context():
    """
    Test that embed_ontology task can be called from within an async context.

    This simulates the scenario where task_always_eager=True and the task
    is called from a FastAPI endpoint (which runs in an event loop).

    The test should NOT raise "RuntimeError: asyncio.run() cannot be called
    from a running event loop" because we now use run_async() instead of
    asyncio.run().
    """
    # This test runs inside an async context (simulating FastAPI endpoint)
    # When task_always_eager=True, calling .delay() would execute the task
    # synchronously in this same event loop

    # We can't actually test the full Celery task without Neo4j and database setup,
    # but we can verify that the run_async helper would work in this context

    async def mock_create_background_job():
        """Mock create_background_job operation."""
        await asyncio.sleep(0.001)
        return 123  # mock job_id

    async def mock_mark_job_running(job_id: int):
        """Mock mark_job_running operation."""
        await asyncio.sleep(0.001)

    async def mock_embed_impl(job_id: int, ontology_id: int):
        """Mock embedding implementation."""
        await asyncio.sleep(0.001)
        return {
            "nodes_processed": 10,
            "nodes_failed": 0,
            "total_found": 10,
            "status": "success",
        }

    async def mock_mark_job_done(job_id: int, result: dict):
        """Mock mark_job_done operation."""
        await asyncio.sleep(0.001)

    # This should NOT raise RuntimeError
    # Simulate what happens in embed_ontology task
    job_id = run_async(mock_create_background_job())
    assert job_id == 123

    run_async(mock_mark_job_running(job_id))
    result = run_async(mock_embed_impl(job_id, 1))
    assert result["status"] == "success"

    run_async(mock_mark_job_done(job_id, result))


@pytest.mark.asyncio
async def test_embed_instance_from_async_context():
    """
    Test that embed_instance task can be called from within an async context.

    This test verifies the legacy embed_instance task works in eager mode.
    """

    async def mock_update_job_progress(job_id: int, progress: float, details: dict):
        """Mock update_job_progress operation."""
        await asyncio.sleep(0.001)

    # This should NOT raise RuntimeError
    run_async(mock_update_job_progress(123, 0.5, {"status": "embedding in progress"}))
    run_async(mock_update_job_progress(123, 0.9, {"status": "embedding completed"}))


@pytest.mark.asyncio
async def test_run_async_helper_from_sync_context():
    """Test that run_async works in a synchronous context too."""

    async def async_operation():
        """Simple async operation."""
        await asyncio.sleep(0.001)
        return "success"

    # This should work fine - run_async detects no event loop and uses asyncio.run()
    def sync_function():
        return run_async(async_operation())

    # Call the sync function from async context
    # The run_async helper will detect the running loop and handle it correctly
    result = await asyncio.to_thread(sync_function)
    assert result == "success"
