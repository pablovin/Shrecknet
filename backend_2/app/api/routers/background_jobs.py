"""API endpoints for background jobs."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user, get_current_user
from app.db.jobs_session import get_jobs_session
from app.models.background_job import AuthorType, JobStatus, JobType
from app.models.user import User
from app.schemas.background_job import (
    BackgroundJobCreate,
    BackgroundJobResponse,
    BackgroundJobUpdate,
)
from app.services.background_job_service import BackgroundJobService

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _job_to_frontend_format(job: BackgroundJobResponse) -> dict[str, Any]:
    """Convert internal job format to frontend-compatible format."""
    return {
        "kind": job.job_type,
        "job_id": str(job.id),
        "start_time": job.started_at.isoformat(),
        "status": job.status,
        "author_type": job.author_type,
        "author_id": job.author_id,
        "description": job.description,
        "details": job.details,
        "progress": job.progress,
        "error_message": job.error_message,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "duration_seconds": job.duration_seconds,
        "ontology_id": job.ontology_id,
        "updated_at": job.updated_at.isoformat(),
    }


@router.get("/", response_model=list[dict[str, Any]])
async def list_jobs(
    jobs_session: Annotated[AsyncSession, Depends(get_jobs_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    author_type: AuthorType | None = Query(None),
    author_id: str | None = Query(None),
    job_type: JobType | None = Query(None),
    status: JobStatus | None = Query(None),
    ontology_id: int | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """
    List background jobs with optional filtering.

    Returns jobs in a format compatible with the frontend.
    Requires authentication.
    """
    service = BackgroundJobService(jobs_session)
    jobs = await service.list_jobs(
        author_type=author_type,
        author_id=author_id,
        job_type=job_type,
        status=status,
        ontology_id=ontology_id,
        limit=limit,
        offset=offset,
    )
    return [_job_to_frontend_format(job) for job in jobs]


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


@router.post(
    "/", response_model=BackgroundJobResponse, status_code=status.HTTP_201_CREATED
)
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


class FrontendDeleteRequest(BaseModel):
    """Frontend-compatible delete request format."""

    jobs: list[dict[str, str]]  # [{"kind": "...", "job_id": "..."}]


@router.delete("/", status_code=status.HTTP_200_OK)
async def delete_jobs(
    delete_request: FrontendDeleteRequest,
    jobs_session: Annotated[AsyncSession, Depends(get_jobs_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, int]:
    """
    Delete multiple completed or failed background jobs.

    Accepts frontend format: {"jobs": [{"kind": "...", "job_id": "..."}, ...]}
    Only jobs with status 'done' or 'failed' can be deleted.
    Requires authentication.
    """
    # Convert frontend format to internal format
    job_ids = [int(job["job_id"]) for job in delete_request.jobs]

    service = BackgroundJobService(jobs_session)
    deleted_count = await service.delete_jobs(job_ids)
    return {"deleted_count": deleted_count}


@router.delete("/admin/clear-all", status_code=status.HTTP_200_OK)
async def clear_all_background_jobs(
    jobs_session: Annotated[AsyncSession, Depends(get_jobs_session)],
    current_user: Annotated[User, Depends(get_current_admin_user)],
    job_type: JobType | None = Query(None, description="Filter by job type"),
    status_filter: JobStatus | None = Query(
        None, description="Filter by status", alias="status"
    ),
    ontology_id: int | None = Query(None, description="Filter by ontology_id"),
) -> dict[str, Any]:
    """
    Clear all background jobs, optionally filtered by type, status, or ontology.

    This deletes all jobs matching the filters. Use with caution!
    Only jobs with status 'done' or 'failed' can be deleted unless no status filter is provided.

    Requires admin role.
    """
    from sqlalchemy import delete, select
    from app.models.background_job import BackgroundJob

    # Build the query to find jobs to delete
    query = select(BackgroundJob)

    if job_type:
        query = query.where(BackgroundJob.job_type == job_type)
    if status_filter:
        query = query.where(BackgroundJob.status == status_filter)
    else:
        # If no status filter, only delete done or failed jobs for safety
        query = query.where(
            BackgroundJob.status.in_([JobStatus.DONE, JobStatus.FAILED])
        )
    if ontology_id is not None:
        query = query.where(BackgroundJob.ontology_id == ontology_id)

    # Get the jobs to delete
    result = await jobs_session.execute(query)
    jobs_to_delete = list(result.scalars().all())

    # Delete the jobs
    if jobs_to_delete:
        delete_query = delete(BackgroundJob).where(
            BackgroundJob.id.in_([job.id for job in jobs_to_delete])
        )
        await jobs_session.execute(delete_query)
        await jobs_session.commit()

    return {
        "message": f"Cleared {len(jobs_to_delete)} background jobs",
        "deleted_count": len(jobs_to_delete),
        "job_type": job_type,
        "status": status_filter,
        "ontology_id": ontology_id,
    }
