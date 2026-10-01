"""Regression tests for the Novelist Celery entry point."""

import asyncio
from unittest.mock import AsyncMock

import app.tasks.novelist as novelist_task


class _SessionContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    commit = AsyncMock()


def test_generate_draft_uses_background_job_id_returned_by_tracker(monkeypatch) -> None:
    """The tracking helper returns an integer rather than a job model."""
    session = _SessionContext()
    repository = type("Repository", (), {"attach_background_job": AsyncMock()})()
    result = {"status": "completed"}

    monkeypatch.setattr(novelist_task, "AsyncSessionMaker", lambda: session)
    monkeypatch.setattr(novelist_task, "NovelistRepository", lambda _: repository)
    monkeypatch.setattr(novelist_task, "create_background_job", AsyncMock(return_value=42))
    monkeypatch.setattr(novelist_task, "mark_job_running", AsyncMock())
    monkeypatch.setattr(novelist_task, "_execute_run", AsyncMock(return_value=result))
    monkeypatch.setattr(novelist_task, "run_async", lambda coroutine: asyncio.run(coroutine))

    assert novelist_task.generate_draft.run("run-123", {"source_text": "A note"}) == result
    repository.attach_background_job.assert_awaited_once_with("run-123", 42)
    novelist_task.mark_job_running.assert_awaited_once_with(42)
    novelist_task._execute_run.assert_awaited_once_with(
        run_id="run-123",
        request_payload={"source_text": "A note"},
        job_id=42,
    )
