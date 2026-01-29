"""
Additional test to verify the fix handles async context managers properly.
This simulates the Neo4j driver session pattern from the problem statement.
"""

from __future__ import annotations

import asyncio

import pytest

from app.utils.async_helpers import run_async


class MockAsyncContextManager:
    """Mock async context manager that simulates Neo4j session behavior."""

    def __init__(self):
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        """Enter the context."""
        await asyncio.sleep(0.001)  # Simulate async work
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the context - this is where the error was happening."""
        # Simulate async cleanup operations (like Neo4j connection reset)
        await asyncio.sleep(0.001)
        self.exited = True
        return False

    async def do_work(self):
        """Simulate async work within the context."""
        await asyncio.sleep(0.001)
        return "work done"


@pytest.mark.asyncio
async def test_async_context_manager_in_nested_loop():
    """
    Test async context managers work correctly when run_async creates a new loop.

    This simulates the exact pattern from the problem:
    - FastAPI endpoint (async context) calls Celery task
    - Celery task (with task_always_eager=True) calls run_async
    - run_async creates new event loop in thread
    - Code uses async context manager (like Neo4j session)
    - Context manager __aexit__ does async cleanup
    """

    async def use_context_manager():
        """Simulate the pattern from ontology_links.py."""
        mgr = MockAsyncContextManager()
        async with mgr as ctx:
            result = await ctx.do_work()
            assert mgr.entered
            assert not mgr.exited
        # After exiting context
        assert mgr.exited
        return result

    # This simulates calling from FastAPI endpoint (already in event loop)
    # and then calling Celery task with task_always_eager=True
    result = run_async(use_context_manager())
    assert result == "work done"


@pytest.mark.asyncio
async def test_multiple_nested_context_managers():
    """Test with multiple nested async context managers."""

    async def nested_contexts():
        """Use multiple nested context managers."""
        mgr1 = MockAsyncContextManager()
        mgr2 = MockAsyncContextManager()

        async with mgr1:
            async with mgr2:
                result1 = await mgr1.do_work()
                result2 = await mgr2.do_work()

        assert mgr1.exited
        assert mgr2.exited
        return f"{result1}, {result2}"

    result = run_async(nested_contexts())
    assert result == "work done, work done"


@pytest.mark.asyncio
async def test_context_manager_with_exception():
    """Test that exceptions in context managers are properly propagated."""

    class FailingContextManager(MockAsyncContextManager):
        async def do_work(self):
            """Raise an exception during work."""
            raise ValueError("Task failed")

    async def use_failing_context():
        """Use a context manager that raises."""
        async with FailingContextManager() as ctx:
            await ctx.do_work()

    with pytest.raises(ValueError, match="Task failed"):
        run_async(use_failing_context())


@pytest.mark.asyncio
async def test_context_manager_cleanup_with_exception():
    """Test that exceptions during cleanup are properly propagated."""

    class FailingCleanupManager(MockAsyncContextManager):
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            """Raise an exception during cleanup."""
            await asyncio.sleep(0.001)
            raise RuntimeError("Cleanup failed")

    async def use_failing_cleanup():
        """Use a context manager with failing cleanup."""
        async with FailingCleanupManager() as ctx:
            await ctx.do_work()

    with pytest.raises(RuntimeError, match="Cleanup failed"):
        run_async(use_failing_cleanup())
