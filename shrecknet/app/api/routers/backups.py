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
from fastapi.responses import FileResponse, StreamingResponse

from app.api.deps import get_current_admin_user
from app.graph.neo4j import get_neo4j_session
from app.models.background_job import AuthorType, JobType
from app.models.user import User
from app.services.backup_service import BackupService
from app.services.legacy_monolith_import_service import LegacyMonolithImportService
from app.tasks.backup_tasks import create_backup_task, import_legacy_backup_task, restore_backup_task
from app.utils.async_helpers import run_async
from app.utils.job_tracking import create_background_job
from neo4j import AsyncSession as AsyncNeo4jSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backups", tags=["backups"])


def _iter_file_chunks(path: Path, chunk_size: int = 1024 * 1024):
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            yield chunk


@router.post("/import-old-db", status_code=status.HTTP_202_ACCEPTED)
async def import_old_db_backup(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_admin_user),
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
) -> dict[str, Any]:
    del graph_session
    if not file.filename or not (file.filename.endswith(".zip") or file.filename.endswith(".tar.gz")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Expected a .zip or .tar.gz backup file",
        )
    payload = await file.read()
    service = LegacyMonolithImportService()
    archive_path = service.get_uploaded_backup_path(file.filename)
    archive_path.write_bytes(payload)
    job_id = run_async(
        create_background_job(
            author_type=AuthorType.USER,
            author_id=str(current_user.id),
            job_type=JobType.LEGACY_IMPORT,
            description=f"Importing legacy monolith backup {archive_path.name}",
            details={
                "admin_user_id": current_user.id,
                "backup_path": str(archive_path),
                "phase": "queued",
                "status": "Queued legacy import",
            },
        )
    )
    task = import_legacy_backup_task.delay(
        backup_path=str(archive_path),
        author_type="user",
        author_id=str(current_user.id),
        admin_user_id=current_user.id,
        existing_job_id=job_id,
    )
    return {
        "job_id": job_id,
        "celery_task_id": task.id,
        "status": "queued",
        "job_type": "legacy_import",
        "message": "Legacy import job created successfully. Monitor the background jobs to track progress.",
    }


@router.post("/create", status_code=status.HTTP_202_ACCEPTED)
async def create_backup(
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
) -> StreamingResponse:
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

        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
        }
        return StreamingResponse(
            _iter_file_chunks(backup_path),
            media_type="application/gzip",
            headers=headers,
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


@router.get("/{filename}")
async def download_backup_compat(
    filename: str,
    current_user: User = Depends(get_current_admin_user),
) -> StreamingResponse:
    return await download_backup(filename=filename, current_user=current_user)


@router.delete("/{filename}", status_code=status.HTTP_200_OK)
async def delete_backup(
    filename: str,
    current_user: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    del current_user
    try:
        backup_service = BackupService()
        return backup_service.delete_backup(filename)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Backup not found: {filename}",
        )
    except Exception as e:
        logger.error(f"Failed to delete backup {filename}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete backup: {str(e)}",
        )


@router.post("/restore", status_code=status.HTTP_202_ACCEPTED)
async def restore_backup(
    file: UploadFile = File(...),
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

    backup_service = BackupService()
    persisted_path = backup_service.get_uploaded_backup_path(file.filename)

    try:
        # Write uploaded file to disk in a shared persistent location
        with open(persisted_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Launch Celery task for restore
        task = restore_backup_task.delay(
            backup_path=str(persisted_path),
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
            "stored_path": str(persisted_path),
        }

    except Exception as e:
        # Clean up persisted file on error
        if persisted_path.exists():
            persisted_path.unlink()
        logger.error(f"Failed to create restore task: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create restore task: {str(e)}",
        )
