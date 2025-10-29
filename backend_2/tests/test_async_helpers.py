"""Tests for async_helpers module."""

from __future__ import annotations

import asyncio

import pytest

from app.utils.async_helpers import run_async


async def simple_async_func(value: int) -> int:
    """Simple async function for testing."""
    await asyncio.sleep(0.001)
    return value * 2


async def async_func_that_raises() -> None:
    """Async function that raises an exception."""
    await asyncio.sleep(0.001)
    raise ValueError("Test error")


def test_run_async_without_event_loop():
    """Test run_async when no event loop is running."""
    result = run_async(simple_async_func(5))
    assert result == 10


def test_run_async_with_exception():
    """Test run_async properly propagates exceptions."""
    with pytest.raises(ValueError, match="Test error"):
        run_async(async_func_that_raises())


@pytest.mark.asyncio
async def test_run_async_within_event_loop():
    """Test run_async when an event loop is already running."""
    # This test runs inside an async context (event loop is running)
    # The function should handle this case by running in a separate thread
    result = run_async(simple_async_func(7))
    assert result == 14


@pytest.mark.asyncio
async def test_run_async_within_event_loop_with_exception():
    """Test run_async properly propagates exceptions when event loop is running."""
    with pytest.raises(ValueError, match="Test error"):
        run_async(async_func_that_raises())
