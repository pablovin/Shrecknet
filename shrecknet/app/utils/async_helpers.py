"""Helpers for running async code in synchronous contexts (e.g., Celery tasks)."""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


def _is_benign_loop_shutdown_error(context: dict[str, Any]) -> bool:
    message = str(context.get("message") or "")
    exc = context.get("exception")
    future = context.get("future")

    if "Task exception was never retrieved" not in message:
        return False
    if not isinstance(exc, RuntimeError):
        return False
    if "event loop is closed" not in str(exc).lower():
        return False

    # httpx/httpcore may schedule AsyncClient.aclose tasks while the loop is shutting down.
    return "AsyncClient.aclose" in str(future)


def _run_coro_in_new_loop(coro: Coroutine[Any, Any, T]) -> T:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _exception_handler(current_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        if _is_benign_loop_shutdown_error(context):
            return
        current_loop.default_exception_handler(context)

    loop.set_exception_handler(_exception_handler)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            # Python 3.11: best-effort shutdown of default executor threads.
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()
            asyncio.set_event_loop(None)


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
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop is running, run in an isolated managed loop.
        return _run_coro_in_new_loop(coro)
    else:
        # A loop is already running (e.g., when task_always_eager=True)
        # We need to run the coroutine in a separate thread with its own loop
        def run_in_thread() -> T:
            return _run_coro_in_new_loop(coro)
        
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(run_in_thread)
            return future.result()
