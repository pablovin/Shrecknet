"""API endpoints for background jobs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.jobs_session import get_jobs_session
from app.models.background_job import AuthorType, JobStatus, JobType
from app.models.user import User
from app.schemas.background_job import (
    BackgroundJobCreate,
    BackgroundJobDeleteRequest,
    BackgroundJobResponse,
    BackgroundJobUpdate,
)
from app.services.background_job_service import BackgroundJobService

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/", response_model=list[BackgroundJobResponse])
async def list_jobs(
    jobs_session: Annotated[AsyncSession, Depends(get_jobs_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    author_type: AuthorType | None = Query(None),
    author_id: str | None = Query(None),
    job_type: JobType | None = Query(None),
    status: JobStatus | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[BackgroundJobResponse]:
    """
    List background jobs with optional filtering.

    Requires authentication.
    """
    service = BackgroundJobService(jobs_session)
    return await service.list_jobs(
        author_type=author_type,
        author_id=author_id,
        job_type=job_type,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=BackgroundJobResponse)
async def get_job(
    job_id: int,
    jobs_session: Annotated[AsyncSession, Depends(get_jobs_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> BackgroundJobResponse:
    """
    Get a specific background job by ID.

    Requires authentication.
    """
    service = BackgroundJobService(jobs_session)
    job = await service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Background job {job_id} not found",
        )
    return job


@router.post("/", response_model=BackgroundJobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    job_data: BackgroundJobCreate,
    jobs_session: Annotated[AsyncSession, Depends(get_jobs_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> BackgroundJobResponse:
    """
    Create a new background job.

    This is primarily for internal use by Celery tasks.
    Requires authentication.
    """
    service = BackgroundJobService(jobs_session)
    return await service.create_job(job_data)


@router.patch("/{job_id}", response_model=BackgroundJobResponse)
async def update_job(
    job_id: int,
    update_data: BackgroundJobUpdate,
    jobs_session: Annotated[AsyncSession, Depends(get_jobs_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> BackgroundJobResponse:
    """
    Update a background job's status or progress.

    This is primarily for internal use by Celery tasks.
    Requires authentication.
    """
    service = BackgroundJobService(jobs_session)
    job = await service.update_job(job_id, update_data)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Background job {job_id} not found",
        )
    return job


@router.delete("/", status_code=status.HTTP_200_OK)
async def delete_jobs(
    delete_request: BackgroundJobDeleteRequest,
    jobs_session: Annotated[AsyncSession, Depends(get_jobs_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, int]:
    """
    Delete multiple completed or failed background jobs.

    Only jobs with status 'done' or 'failed' can be deleted.
    Requires authentication.
    """
    service = BackgroundJobService(jobs_session)
    deleted_count = await service.delete_jobs(delete_request.job_ids)
    return {"deleted_count": deleted_count}
