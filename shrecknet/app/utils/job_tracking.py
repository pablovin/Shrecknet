"""Celery integration utilities for background job tracking."""

from __future__ import annotations

import asyncio
import json
import logging
from functools import wraps
from typing import Any, Callable, TypeVar

from celery import Task

from app.db.jobs_session import JobsSessionMaker
from app.models.background_job import AuthorType, JobStatus, JobType
from app.repositories.background_job_repository import BackgroundJobRepository

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class JobTrackingTask(Task):
    """
    Custom Celery task that automatically tracks job progress in the database.
    """

    def __init__(self) -> None:
        super().__init__()
        self.job_id: int | None = None

    def before_start(self, task_id: str, args: tuple, kwargs: dict) -> None:
        """Called before task execution."""
        super().before_start(task_id, args, kwargs)
        # Job creation should be handled by the caller, we just mark it as running
        if self.job_id:
            asyncio.run(self._mark_running(self.job_id))

    def on_success(self, retval: Any, task_id: str, args: tuple, kwargs: dict) -> None:
        """Called on task success."""
        super().on_success(retval, task_id, args, kwargs)
        if self.job_id:
            asyncio.run(self._mark_done(self.job_id, retval))

    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: tuple,
        kwargs: dict,
        einfo: Any,
    ) -> None:
        """Called on task failure."""
        super().on_failure(exc, task_id, args, kwargs, einfo)
        if self.job_id:
            asyncio.run(self._mark_failed(self.job_id, str(exc)))

    async def _mark_running(self, job_id: int) -> None:
        """Mark job as running."""
        try:
            async with JobsSessionMaker() as session:
                repo = BackgroundJobRepository(session)
                await repo.mark_as_running(job_id)
        except Exception as e:
            logger.error(f"Failed to mark job {job_id} as running: {e}")

    async def _mark_done(self, job_id: int, result: Any) -> None:
        """Mark job as done."""
        try:
            async with JobsSessionMaker() as session:
                repo = BackgroundJobRepository(session)
                details = None
                if result:
                    try:
                        details = json.dumps(result)
                    except (TypeError, ValueError):
                        details = str(result)
                await repo.mark_as_done(job_id, details=details)
        except Exception as e:
            logger.error(f"Failed to mark job {job_id} as done: {e}")

    async def _mark_failed(self, job_id: int, error: str) -> None:
        """Mark job as failed."""
        try:
            async with JobsSessionMaker() as session:
                repo = BackgroundJobRepository(session)
                await repo.mark_as_failed(job_id, error)
        except Exception as e:
            logger.error(f"Failed to mark job {job_id} as failed: {e}")


async def create_background_job(
    author_type: AuthorType,
    author_id: str,
    job_type: JobType,
    description: str,
    celery_task_id: str | None = None,
    details: dict | None = None,
    ontology_id: int | None = None,
) -> int:
    """
    Create a background job entry in the database.

    Returns the job ID.
    """
    async with JobsSessionMaker() as session:
        repo = BackgroundJobRepository(session)
        details_str = json.dumps(details) if details else None
        job = await repo.create(
            author_type=author_type,
            author_id=author_id,
            job_type=job_type,
            description=description,
            celery_task_id=celery_task_id,
            details=details_str,
            ontology_id=ontology_id,
        )
        return job.id


async def update_job_progress(
    job_id: int, progress: float, details: dict | None = None
) -> None:
    """
    Update the progress of a background job.

    Args:
        job_id: The job ID
        progress: Progress value between 0.0 and 1.0
        details: Optional details dictionary
    """
    async with JobsSessionMaker() as session:
        repo = BackgroundJobRepository(session)
        details_str = json.dumps(details) if details else None
        await repo.update_progress(job_id, progress, details_str)


async def mark_job_running(job_id: int) -> None:
    """Mark a job as running."""
    async with JobsSessionMaker() as session:
        repo = BackgroundJobRepository(session)
        await repo.mark_as_running(job_id)


async def mark_job_done(job_id: int, details: dict | None = None) -> None:
    """Mark a job as completed successfully."""
    async with JobsSessionMaker() as session:
        repo = BackgroundJobRepository(session)
        details_str = json.dumps(details) if details else None
        await repo.mark_as_done(job_id, details_str)


async def mark_job_failed(
    job_id: int,
    error_message: str,
    details: dict | None = None,
) -> None:
    """Mark a job as failed."""
    async with JobsSessionMaker() as session:
        repo = BackgroundJobRepository(session)
        details_str = json.dumps(details) if details else None
        await repo.mark_as_failed(job_id, error_message, details_str)
