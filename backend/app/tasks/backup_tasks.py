"""Celery tasks for backup and restore operations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.celery_app import celery_app
from app.graph.neo4j import get_driver
from app.models.background_job import AuthorType, JobType
from app.services.backup_service import BackupService
from app.services.maintenance_mode_service import MaintenanceModeService
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)

logger = logging.getLogger(__name__)


@celery_app.task(name="backup.create_backup")
def create_backup_task(
    author_type: str = "agent",
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

    current_phase = "queued"
    try:
        # Mark as running
        run_async(mark_job_running(job_id))
        current_phase = "initializing"

        async def report_progress(phase: str, progress: float, status_text: str) -> None:
            nonlocal current_phase
            current_phase = phase
            await update_job_progress(
                job_id,
                progress,
                {
                    "phase": phase,
                    "status": status_text,
                },
            )

        run_async(report_progress("initializing", 0.05, "Initializing backup process"))

        # Create backup using BackupService
        async def perform_backup():
            driver = get_driver()
            async with driver.session() as neo4j_session:
                backup_service = BackupService()
                result = await backup_service.create_backup(
                    neo4j_session=neo4j_session,
                    progress_callback=report_progress,
                )
                await report_progress("finalizing", 0.98, "Backup archive created")
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
                    "backup_kind": result.get("backup_kind"),
                    "storage_path": result.get("storage_path"),
                    "database_files": result.get("database_files"),
                    "neo4j_nodes": result.get("neo4j_nodes"),
                    "neo4j_relationships": result.get("neo4j_relationships"),
                    "media_files": result.get("media_files"),
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
        logger.error("Failed to create backup: %s", str(e), exc_info=True)
        run_async(mark_job_failed(job_id, f"[{current_phase}] {str(e)}"))
        raise


@celery_app.task(name="backup.restore_backup")
def restore_backup_task(
    backup_path: str,
    author_type: str = "agent",
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

    current_phase = "queued"
    try:
        # Mark as running
        run_async(mark_job_running(job_id))
        current_phase = "initializing"
        maintenance_service = MaintenanceModeService()

        async def report_progress(phase: str, progress: float, status_text: str) -> None:
            nonlocal current_phase
            current_phase = phase
            await update_job_progress(
                job_id,
                progress,
                {
                    "phase": phase,
                    "status": status_text,
                },
            )

        run_async(report_progress("initializing", 0.05, "Initializing restore process"))

        # Restore backup using BackupService
        async def perform_restore():
            backup_service = BackupService()
            maintenance_service.enable("destructive_restore")
            try:
                driver = get_driver()
                async with driver.session() as neo4j_session:
                    result = await backup_service.restore_backup(
                        Path(backup_path),
                        neo4j_session=neo4j_session,
                        progress_callback=report_progress,
                    )
                    await report_progress("finalizing", 0.98, "Restore completed")
                    return result
            finally:
                maintenance_service.disable()

        result = run_async(perform_restore())

        # Mark job as complete
        run_async(
            mark_job_done(
                job_id,
                details={
                    "admin_user_id": admin_user_id,
                    "backup_path": backup_path,
                    "restored_at": result.get("restored_at"),
                    "restart_required": result.get("restart_required", False),
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
        logger.error("Failed to restore backup: %s", str(e), exc_info=True)
        run_async(mark_job_failed(job_id, f"[{current_phase}] {str(e)}"))
        raise
