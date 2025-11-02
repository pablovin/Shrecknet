"""Celery tasks for backup and restore operations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.celery_app import celery_app
from app.core.config import get_settings
from app.db.session import AsyncSessionMaker
from app.graph.neo4j import get_driver
from app.models.background_job import AuthorType, JobType
from app.services.backup_service import BackupService
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)

logger = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(name="backup.create_backup")
def create_backup_task(
    author_type: str = "user",
    author_id: str = "system",
    admin_user_id: int | None = None,
) -> dict[str, Any]:
    """
    Create a complete backup of all data as a background task.

    This task:
    1. Exports all SQLAlchemy database tables to JSON
    2. Exports Neo4j graph data
    3. Copies all media files
    4. Creates a tar.gz archive

    Args:
        author_type: Type of author triggering the task (user/agent)
        author_id: ID of the author
        admin_user_id: ID of the admin user who triggered the backup

    Returns:
        Dictionary with job_id, backup metadata, and status
    """
    # Create job entry
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.BACKUP,
            description="Creating system backup",
            celery_task_id=create_backup_task.request.id,
            details={
                "admin_user_id": admin_user_id,
            },
        )
    )

    try:
        # Mark as running
        run_async(mark_job_running(job_id))

        # Update progress: Starting backup
        run_async(
            update_job_progress(
                job_id, 0.1, {"status": "Initializing backup process"}
            )
        )

        # Create backup using BackupService
        async def perform_backup():
            async with AsyncSessionMaker() as db_session:
                driver = get_driver()
                async with driver.session() as neo4j_session:
                    backup_service = BackupService()
                    
                    # Update progress: Exporting database
                    await update_job_progress(
                        job_id, 0.2, {"status": "Exporting database"}
                    )
                    
                    result = await backup_service.create_backup(
                        db_session, neo4j_session
                    )
                    
                    # Update progress: Backup complete
                    await update_job_progress(
                        job_id, 0.9, {"status": "Backup archive created"}
                    )
                    
                    return result

        result = run_async(perform_backup())

        # Mark job as complete
        run_async(
            mark_job_done(
                job_id,
                details={
                    "admin_user_id": admin_user_id,
                    "backup_filename": result.get("filename"),
                    "backup_size": result.get("size_bytes"),
                    "database_records": result.get("database_records"),
                    "neo4j_nodes": result.get("neo4j_nodes"),
                    "neo4j_relationships": result.get("neo4j_relationships"),
                },
            )
        )

        logger.info(
            f"Backup created successfully by user {author_id}: {result.get('filename')}"
        )

        return {
            "job_id": job_id,
            "status": "completed",
            **result,
        }

    except Exception as e:
        logger.error(f"Failed to create backup: {str(e)}", exc_info=True)
        run_async(mark_job_failed(job_id, str(e)))
        raise


@celery_app.task(name="backup.restore_backup")
def restore_backup_task(
    backup_path: str,
    author_type: str = "user",
    author_id: str = "system",
    admin_user_id: int | None = None,
) -> dict[str, Any]:
    """
    Restore from a backup file as a background task.

    This task:
    1. Clears all existing data
    2. Restores database records
    3. Restores Neo4j graph
    4. Restores media files
    5. Preserves the admin user who invoked the restore

    Args:
        backup_path: Path to the backup tar.gz file
        author_type: Type of author triggering the task (user/agent)
        author_id: ID of the author
        admin_user_id: ID of the admin user who triggered the restore

    Returns:
        Dictionary with job_id and restoration status
    """
    # Create job entry
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.RESTORE,
            description=f"Restoring backup from {Path(backup_path).name}",
            celery_task_id=restore_backup_task.request.id,
            details={
                "admin_user_id": admin_user_id,
                "backup_path": backup_path,
            },
        )
    )

    try:
        # Mark as running
        run_async(mark_job_running(job_id))

        # Update progress: Starting restore
        run_async(
            update_job_progress(
                job_id, 0.1, {"status": "Initializing restore process"}
            )
        )

        # Restore backup using BackupService
        async def perform_restore():
            async with AsyncSessionMaker() as db_session:
                driver = get_driver()
                async with driver.session() as neo4j_session:
                    backup_service = BackupService()
                    
                    # Update progress: Extracting backup
                    await update_job_progress(
                        job_id, 0.2, {"status": "Extracting backup archive"}
                    )
                    
                    result = await backup_service.restore_backup(
                        Path(backup_path), db_session, neo4j_session, admin_user_id
                    )
                    
                    # Update progress: Restore complete
                    await update_job_progress(
                        job_id, 0.9, {"status": "Restore completed"}
                    )
                    
                    return result

        result = run_async(perform_restore())

        # Mark job as complete
        run_async(
            mark_job_done(
                job_id,
                details={
                    "admin_user_id": admin_user_id,
                    "backup_path": backup_path,
                    "restored_at": result.get("restored_at"),
                },
            )
        )

        logger.info(
            f"Backup restored successfully by user {author_id} from {backup_path}"
        )

        return {
            "job_id": job_id,
            "status": "completed",
            **result,
        }

    except Exception as e:
        logger.error(f"Failed to restore backup: {str(e)}", exc_info=True)
        run_async(mark_job_failed(job_id, str(e)))
        raise
