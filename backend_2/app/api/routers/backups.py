"""
Backup and restore API endpoints.

Provides endpoints to:
- Create a backup of all data (database, Neo4j, media files) as a background job
- List available backups
- Download a backup file
- Restore from an uploaded backup file as a background job
- Monitor backup/restore job status
"""

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user, get_db_session
from app.models.user import User
from app.repositories.background_job_repository import BackgroundJobRepository
from app.services.backup_service import BackupService
from app.tasks.backup_tasks import create_backup_task, restore_backup_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backups", tags=["backups"])


@router.post("/create", status_code=status.HTTP_202_ACCEPTED)
async def create_backup(
    db_session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    """
    Create a complete backup of all data as a background job.

    This endpoint creates a background job that will:
    - Export all database tables (as JSON)
    - Export all Neo4j graph data (nodes and relationships as JSON)
    - Copy all media files
    - Create a tar.gz archive in /media/backups/

    **Requires admin role.**

    Returns:
        Background job information including job_id for monitoring progress

    Example:
        POST /backups/create
        Response (202):
        {
            "job_id": 123,
            "status": "queued",
            "message": "Backup job created successfully. Use /jobs/123 to monitor progress."
        }
    """
    try:
        # Launch Celery task
        task = create_backup_task.delay(
            author_type="user",
            author_id=str(current_user.id),
            admin_user_id=current_user.id,
        )

        logger.info(
            f"Backup task created by user {current_user.username} (celery_task_id: {task.id})"
        )

        # Get the job_id from the repository (the task creates it)
        # For now, we'll return the celery task id
        return {
            "celery_task_id": task.id,
            "status": "queued",
            "message": "Backup job created successfully. Monitor the background jobs to track progress.",
        }
    except Exception as e:
        logger.error(f"Failed to create backup task: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create backup task: {str(e)}",
        )


@router.get("/")
async def list_backups(
    current_user: User = Depends(get_current_admin_user),
) -> list[dict[str, Any]]:
    """
    List all available backups.

    **Requires admin role.**

    Returns:
        List of backup metadata (filename, size, creation time)
    """
    try:
        backup_service = BackupService()
        backups = backup_service.list_backups()
        return backups
    except Exception as e:
        logger.error(f"Failed to list backups: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list backups: {str(e)}",
        )


@router.get("/{filename}/download")
async def download_backup(
    filename: str,
    current_user: User = Depends(get_current_admin_user),
) -> FileResponse:
    """
    Download a backup file.

    **Requires admin role.**

    Args:
        filename: Name of the backup file (e.g., backup_20231202_153045.tar.gz)

    Returns:
        The backup file for download
    """
    try:
        backup_service = BackupService()
        backup_path = backup_service.get_backup_path(filename)

        return FileResponse(
            path=str(backup_path),
            filename=filename,
            media_type="application/gzip",
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Backup not found: {filename}",
        )
    except Exception as e:
        logger.error(f"Failed to download backup: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to download backup: {str(e)}",
        )


@router.post("/restore", status_code=status.HTTP_202_ACCEPTED)
async def restore_backup(
    file: UploadFile = File(...),
    db_session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    """
    Restore from an uploaded backup file as a background job.

    This endpoint will create a background job that:
    1. **DELETE ALL EXISTING DATA** (database, Neo4j, media files)
    2. Restore data from the uploaded backup file
    3. **PRESERVE the admin user who invoked the restore** (won't be replaced)

    **WARNING: This is a destructive operation. All current data will be lost.**

    **Requires admin role.**

    Args:
        file: The backup tar.gz file to restore from

    Returns:
        Background job information including job_id for monitoring progress

    Example:
        POST /backups/restore
        Response (202):
        {
            "job_id": 124,
            "celery_task_id": "abc-123-def",
            "status": "queued",
            "message": "Restore job created successfully. Use /jobs/124 to monitor progress.",
            "temp_path": "/tmp/backup_20231202_153045.tar.gz"
        }
    """
    if not file.filename or not file.filename.endswith(".tar.gz"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Expected a .tar.gz backup file",
        )

    # Save uploaded file to temporary location
    temp_path = Path("/tmp") / file.filename
    try:
        # Write uploaded file to disk
        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Launch Celery task for restore
        task = restore_backup_task.delay(
            backup_path=str(temp_path),
            author_type="user",
            author_id=str(current_user.id),
            admin_user_id=current_user.id,
        )

        logger.info(
            f"Restore task created by user {current_user.username}: {file.filename} (celery_task_id: {task.id})"
        )

        return {
            "celery_task_id": task.id,
            "status": "queued",
            "message": "Restore job created successfully. Monitor the background jobs to track progress.",
            "temp_path": str(temp_path),
        }

    except Exception as e:
        # Clean up temp file on error
        if temp_path.exists():
            temp_path.unlink()
        logger.error(f"Failed to create restore task: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create restore task: {str(e)}",
        )
