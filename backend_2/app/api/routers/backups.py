"""
Backup and restore API endpoints.

Provides endpoints to:
- Create a backup of all data (database, Neo4j, media files)
- List available backups
- Download a backup file
- Restore from an uploaded backup file
"""

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from neo4j import AsyncSession as Neo4jSession
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user, get_db_session
from app.graph.neo4j import get_neo4j_session
from app.models.user import User
from app.services.backup_service import BackupService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backups", tags=["backups"])


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_backup(
    db_session: AsyncSession = Depends(get_db_session),
    neo4j_session: Neo4jSession = Depends(get_neo4j_session),
    current_user: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    """
    Create a complete backup of all data.

    This endpoint creates a tar.gz archive containing:
    - All database tables (as JSON)
    - All Neo4j graph data (nodes and relationships as JSON)
    - All media files

    The backup is stored in /media/backups/ and can be downloaded later.

    **Requires admin role.**

    Returns:
        Backup metadata including filename, size, and creation time
    """
    try:
        backup_service = BackupService()
        result = await backup_service.create_backup(db_session, neo4j_session)
        logger.info(
            f"Backup created by user {current_user.username}: {result['filename']}"
        )
        return result
    except Exception as e:
        logger.error(f"Failed to create backup: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create backup: {str(e)}",
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


@router.post("/restore", status_code=status.HTTP_200_OK)
async def restore_backup(
    file: UploadFile = File(...),
    db_session: AsyncSession = Depends(get_db_session),
    neo4j_session: Neo4jSession = Depends(get_neo4j_session),
    current_user: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    """
    Restore from an uploaded backup file.

    This endpoint will:
    1. **DELETE ALL EXISTING DATA** (database, Neo4j, media files)
    2. Restore data from the uploaded backup file

    **WARNING: This is a destructive operation. All current data will be lost.**

    **Requires admin role.**

    Args:
        file: The backup tar.gz file to restore from

    Returns:
        Restoration status and metadata
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

        # Restore from the file
        backup_service = BackupService()
        result = await backup_service.restore_backup(
            temp_path, db_session, neo4j_session
        )

        logger.info(f"Backup restored by user {current_user.username}: {file.filename}")
        return result

    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to restore backup: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to restore backup: {str(e)}",
        )
    finally:
        # Clean up temporary file
        if temp_path.exists():
            temp_path.unlink()
