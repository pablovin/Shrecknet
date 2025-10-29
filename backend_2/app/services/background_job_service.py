"""Service layer for background jobs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.background_job import AuthorType, JobStatus, JobType
from app.repositories.background_job_repository import BackgroundJobRepository
from app.schemas.background_job import (
    BackgroundJobCreate,
    BackgroundJobResponse,
    BackgroundJobUpdate,
)


class BackgroundJobService:
    """Business logic for background jobs."""

    def __init__(self, session: AsyncSession):
        self.repo = BackgroundJobRepository(session)

    async def create_job(self, job_data: BackgroundJobCreate) -> BackgroundJobResponse:
        """Create a new background job."""
        job = await self.repo.create(
            author_type=job_data.author_type,
            author_id=job_data.author_id,
            job_type=job_data.job_type,
            description=job_data.description,
            celery_task_id=job_data.celery_task_id,
            details=job_data.details,
        )
        return BackgroundJobResponse.model_validate(job)

    async def get_job(self, job_id: int) -> BackgroundJobResponse | None:
        """Get a background job by ID."""
        job = await self.repo.get_by_id(job_id)
        if not job:
            return None
        return BackgroundJobResponse.model_validate(job)

    async def list_jobs(
        self,
        author_type: AuthorType | None = None,
        author_id: str | None = None,
        job_type: JobType | None = None,
        status: JobStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BackgroundJobResponse]:
        """List background jobs with optional filtering."""
        jobs = await self.repo.list_jobs(
            author_type=author_type,
            author_id=author_id,
            job_type=job_type,
            status=status,
            limit=limit,
            offset=offset,
        )
        return [BackgroundJobResponse.model_validate(job) for job in jobs]

    async def update_job(
        self, job_id: int, update_data: BackgroundJobUpdate
    ) -> BackgroundJobResponse | None:
        """Update a background job."""
        job = await self.repo.get_by_id(job_id)
        if not job:
            return None

        if update_data.status is not None:
            job = await self.repo.update_status(
                job_id=job_id,
                status=update_data.status,
                progress=update_data.progress,
                error_message=update_data.error_message,
                completed_at=update_data.completed_at,
            )
        elif update_data.progress is not None:
            job = await self.repo.update_progress(
                job_id=job_id,
                progress=update_data.progress,
                details=update_data.details,
            )

        if not job:
            return None

        return BackgroundJobResponse.model_validate(job)

    async def delete_jobs(self, job_ids: list[int]) -> int:
        """Delete multiple background jobs. Returns count of deleted jobs."""
        return await self.repo.delete_jobs(job_ids)
