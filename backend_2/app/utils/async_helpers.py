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
        def run_in_thread():
            # Create a new event loop for this thread
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return new_loop.run_until_complete(coro)
            finally:
                try:
                    # Clean up any remaining tasks
                    pending = asyncio.all_tasks(new_loop)
                    for task in pending:
                        task.cancel()
                    # Wait for task cancellations to complete
                    if pending:
                        new_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    # Shutdown async generators
                    new_loop.run_until_complete(new_loop.shutdown_asyncgens())
                finally:
                    new_loop.close()
                    asyncio.set_event_loop(None)
        
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(run_in_thread)
            return future.result()
