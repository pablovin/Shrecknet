"""Repository for background jobs data access."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.background_job import BackgroundJob, JobStatus, JobType, AuthorType


class BackgroundJobRepository:
    """Data access layer for background jobs."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        author_type: AuthorType,
        author_id: str,
        job_type: JobType,
        description: str,
        celery_task_id: str | None = None,
        details: str | None = None,
    ) -> BackgroundJob:
        """Create a new background job."""
        job = BackgroundJob(
            celery_task_id=celery_task_id,
            author_type=author_type,
            author_id=author_id,
            job_type=job_type,
            description=description,
            details=details,
            status=JobStatus.QUEUED,
            progress=0.0,
        )
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def get_by_id(self, job_id: int) -> BackgroundJob | None:
        """Get a background job by ID."""
        result = await self.session.execute(
            select(BackgroundJob).where(BackgroundJob.id == job_id)
        )
        return result.scalar_one_or_none()

    async def get_by_celery_task_id(self, celery_task_id: str) -> BackgroundJob | None:
        """Get a background job by Celery task ID."""
        result = await self.session.execute(
            select(BackgroundJob).where(BackgroundJob.celery_task_id == celery_task_id)
        )
        return result.scalar_one_or_none()

    async def list_jobs(
        self,
        author_type: AuthorType | None = None,
        author_id: str | None = None,
        job_type: JobType | None = None,
        status: JobStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BackgroundJob]:
        """List background jobs with optional filtering."""
        query = select(BackgroundJob)

        if author_type:
            query = query.where(BackgroundJob.author_type == author_type)
        if author_id:
            query = query.where(BackgroundJob.author_id == author_id)
        if job_type:
            query = query.where(BackgroundJob.job_type == job_type)
        if status:
            query = query.where(BackgroundJob.status == status)

        query = query.order_by(BackgroundJob.started_at.desc())
        query = query.limit(limit).offset(offset)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update_status(
        self,
        job_id: int,
        status: JobStatus,
        progress: float | None = None,
        error_message: str | None = None,
        completed_at: datetime | None = None,
    ) -> BackgroundJob | None:
        """Update the status of a background job."""
        job = await self.get_by_id(job_id)
        if not job:
            return None

        job.status = status
        if progress is not None:
            job.progress = progress
        if error_message is not None:
            job.error_message = error_message
        if completed_at is not None:
            job.completed_at = completed_at

        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def update_progress(
        self, job_id: int, progress: float, details: str | None = None
    ) -> BackgroundJob | None:
        """Update the progress of a background job."""
        job = await self.get_by_id(job_id)
        if not job:
            return None

        job.progress = progress
        if details is not None:
            job.details = details

        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def mark_as_running(self, job_id: int) -> BackgroundJob | None:
        """Mark a job as running."""
        return await self.update_status(job_id, JobStatus.RUNNING)

    async def mark_as_done(
        self, job_id: int, details: str | None = None
    ) -> BackgroundJob | None:
        """Mark a job as completed successfully."""
        job = await self.get_by_id(job_id)
        if not job:
            return None

        job.status = JobStatus.DONE
        job.progress = 1.0
        job.completed_at = datetime.now(timezone.utc)
        if details is not None:
            job.details = details

        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def mark_as_failed(
        self, job_id: int, error_message: str
    ) -> BackgroundJob | None:
        """Mark a job as failed."""
        job = await self.get_by_id(job_id)
        if not job:
            return None

        job.status = JobStatus.FAILED
        job.error_message = error_message
        job.completed_at = datetime.now(timezone.utc)

        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def delete_jobs(self, job_ids: list[int]) -> int:
        """Delete multiple background jobs by IDs. Returns count of deleted jobs."""
        # Only delete completed or failed jobs
        jobs_to_delete = await self.session.execute(
            select(BackgroundJob).where(
                BackgroundJob.id.in_(job_ids),
                BackgroundJob.status.in_([JobStatus.DONE, JobStatus.FAILED]),
            )
        )
        jobs = list(jobs_to_delete.scalars().all())

        for job in jobs:
            await self.session.delete(job)

        await self.session.commit()
        return len(jobs)
