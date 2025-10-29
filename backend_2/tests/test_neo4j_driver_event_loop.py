"""Tests for Neo4j driver event loop awareness.

This module tests that the Neo4j driver can handle being called from different
event loops, which is critical for Celery tasks that use asyncio.run().
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_neo4j_driver():
    """Provide a mock Neo4j driver."""
    mock_driver = MagicMock()
    mock_driver.close = MagicMock(return_value=asyncio.coroutine(lambda: None)())
    return mock_driver


@pytest.fixture
def mock_settings():
    """Provide mock settings."""
    settings = MagicMock()
    settings.neo4j_uri = "bolt://localhost:7687"
    settings.neo4j_user = "neo4j"
    settings.neo4j_password = "password"
    settings.neo4j_database = "neo4j"
    return settings


def test_driver_recreated_in_different_loops(mock_neo4j_driver, mock_settings):
    """
    Test that the driver is recreated when accessed from different event loops.

    This simulates what happens in Celery tasks when asyncio.run() creates
    a new event loop for each task execution.
    """
    from app.graph import neo4j

    with patch("app.core.config.get_settings", return_value=mock_settings), patch(
        "neo4j.AsyncGraphDatabase.driver", return_value=mock_neo4j_driver
    ):
        # Reset module state
        neo4j._driver = None
        neo4j._driver_loop = None

        # First event loop
        async def get_driver_loop1():
            driver = neo4j.get_driver()
            loop = asyncio.get_running_loop()
            return driver, id(loop)

        driver1, loop1_id = asyncio.run(get_driver_loop1())
        assert neo4j._driver is not None
        assert neo4j._driver_loop is not None
        first_loop_id = id(neo4j._driver_loop)

        # Second event loop (simulates new Celery task)
        async def get_driver_loop2():
            driver = neo4j.get_driver()
            loop = asyncio.get_running_loop()
            return driver, id(loop)

        driver2, loop2_id = asyncio.run(get_driver_loop2())

        # The event loops should be different
        assert loop1_id != loop2_id, "Event loops should be different"

        # The tracked loop should have changed
        second_loop_id = id(neo4j._driver_loop)
        assert (
            first_loop_id != second_loop_id
        ), "Driver loop should have been updated to new loop"


def test_driver_reused_in_same_loop(mock_neo4j_driver, mock_settings):
    """
    Test that the driver is reused when accessed from the same event loop.
    """
    from app.graph import neo4j

    with patch("app.core.config.get_settings", return_value=mock_settings), patch(
        "neo4j.AsyncGraphDatabase.driver", return_value=mock_neo4j_driver
    ):
        # Reset module state
        neo4j._driver = None
        neo4j._driver_loop = None

        async def get_drivers():
            driver1 = neo4j.get_driver()
            driver2 = neo4j.get_driver()
            return driver1, driver2

        d1, d2 = asyncio.run(get_drivers())

        # Should be the same driver instance
        assert d1 is d2, "Driver should be reused in the same event loop"


@pytest.mark.asyncio
async def test_driver_in_async_context(mock_neo4j_driver, mock_settings):
    """
    Test driver creation when already in an async context.

    This tests the scenario where FastAPI endpoints call the driver.
    """
    from app.graph import neo4j

    with patch("app.core.config.get_settings", return_value=mock_settings), patch(
        "neo4j.AsyncGraphDatabase.driver", return_value=mock_neo4j_driver
    ):
        # Reset module state
        neo4j._driver = None
        neo4j._driver_loop = None

        # Get driver in async context
        driver1 = neo4j.get_driver()
        driver2 = neo4j.get_driver()

        assert driver1 is driver2, "Should reuse driver in same async context"


def test_run_async_with_driver_multiple_times(mock_neo4j_driver, mock_settings):
    """
    Test the complete scenario: run_async being called multiple times with driver.

    This simulates the exact Celery worker scenario where multiple tasks
    execute sequentially, each creating a new event loop with asyncio.run().
    """
    from app.graph import neo4j
    from app.utils.async_helpers import run_async

    with patch("app.core.config.get_settings", return_value=mock_settings), patch(
        "neo4j.AsyncGraphDatabase.driver", return_value=mock_neo4j_driver
    ):
        # Reset module state
        neo4j._driver = None
        neo4j._driver_loop = None

        async def simulated_task(task_num: int):
            """Simulate a Celery task that uses the Neo4j driver."""
            driver = neo4j.get_driver()
            # Simulate some async work
            await asyncio.sleep(0.001)
            return f"task_{task_num}_complete"

        # Execute multiple tasks, each with run_async (creates new event loop)
        result1 = run_async(simulated_task(1))
        assert result1 == "task_1_complete"

        result2 = run_async(simulated_task(2))
        assert result2 == "task_2_complete"

        result3 = run_async(simulated_task(3))
        assert result3 == "task_3_complete"

        # All should succeed without "attached to different loop" errors


def test_driver_without_event_loop(mock_neo4j_driver, mock_settings):
    """
    Test driver creation when no event loop is running.

    This can happen in synchronous contexts.
    """
    from app.graph import neo4j

    with patch("app.core.config.get_settings", return_value=mock_settings), patch(
        "neo4j.AsyncGraphDatabase.driver", return_value=mock_neo4j_driver
    ):
        # Reset module state
        neo4j._driver = None
        neo4j._driver_loop = None

        # Get driver without an event loop (synchronous context)
        driver = neo4j.get_driver()

        assert driver is not None
        assert neo4j._driver_loop is None, "Loop should be None when no loop is running"
