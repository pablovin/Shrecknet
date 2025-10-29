"""Pydantic schemas for background jobs API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.background_job import AuthorType, JobStatus, JobType


class BackgroundJobBase(BaseModel):
    """Base schema for background job."""

    author_type: AuthorType
    author_id: str
    job_type: JobType
    description: str
    details: str | None = None


class BackgroundJobCreate(BackgroundJobBase):
    """Schema for creating a background job."""

    celery_task_id: str | None = None


class BackgroundJobUpdate(BaseModel):
    """Schema for updating a background job."""

    status: JobStatus | None = None
    progress: float | None = Field(None, ge=0.0, le=1.0)
    error_message: str | None = None
    details: str | None = None
    completed_at: datetime | None = None


class BackgroundJobResponse(BackgroundJobBase):
    """Schema for background job response."""

    id: int
    celery_task_id: str | None
    status: JobStatus
    progress: float
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BackgroundJobListParams(BaseModel):
    """Query parameters for listing background jobs."""

    author_type: AuthorType | None = None
    author_id: str | None = None
    job_type: JobType | None = None
    status: JobStatus | None = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


class BackgroundJobDeleteRequest(BaseModel):
    """Request to delete multiple background jobs."""

    job_ids: list[int] = Field(..., min_length=1)
