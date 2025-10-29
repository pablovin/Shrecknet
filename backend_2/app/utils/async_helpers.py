"""Helpers for running async code in synchronous contexts (e.g., Celery tasks)."""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """
    Run an async coroutine, handling both cases where an event loop is or isn't running.

    This is useful for Celery tasks that need to run async code, especially when
    task_always_eager=True, which causes tasks to run synchronously in the same
    event loop as the calling code.

    Args:
        coro: The coroutine to run

    Returns:
        The result of the coroutine

    Raises:
        Any exception raised by the coroutine
    """
    try:
        # Try to get the current running loop
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop is running, we can use asyncio.run()
        return asyncio.run(coro)
    else:
        # A loop is already running (e.g., when task_always_eager=True)
        # We need to run the coroutine in a separate thread with its own loop
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
