"""Category-based folder snapshots: sizing, create, restore, export and import."""

from __future__ import annotations

import hmac
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from neo4j import AsyncSession as AsyncNeo4jSession

from app.api.deps import get_current_admin_user
from app.api.routers.backups import SIGNED_DOWNLOAD_TTL_SECONDS, _build_download_signature
from app.graph.neo4j import get_optional_neo4j_session
from app.models.user import User
from app.schemas.backup_snapshot import SnapshotCreateRequest, SnapshotRestoreRequest
from app.services.backup.categories import BackupContext
from app.services.backup.snapshots import BackupBusy, SnapshotError, SnapshotNotFound, SnapshotService
from app.tasks.backup_tasks import create_snapshot_task, restore_snapshot_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backups", tags=["backups"])


def _http_error(exc: SnapshotError) -> HTTPException:
    if isinstance(exc, SnapshotNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, BackupBusy):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/categories")
async def list_backup_categories(
    current_user: User = Depends(get_current_admin_user),
    # Declared after auth so unauthenticated calls never open a Neo4j session.
    neo4j_session: AsyncNeo4jSession | None = Depends(get_optional_neo4j_session),
) -> dict[str, Any]:
    """Backup categories with their current on-disk size, plus free space at the backup root."""
    service = SnapshotService()
    categories = await service.describe_categories(BackupContext(neo4j_session=neo4j_session))
    return {"backup_root": str(service.root), "categories": categories, "disk": service.disk_usage()}


@router.get("/snapshots")
async def list_snapshots(current_user: User = Depends(get_current_admin_user)) -> list[dict[str, Any]]:
    return SnapshotService().list_snapshots()


@router.post("/snapshots", status_code=status.HTTP_202_ACCEPTED)
async def create_snapshot(
    payload: SnapshotCreateRequest,
    current_user: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    service = SnapshotService()
    try:
        service._resolve_selection(payload.categories)
    except SnapshotError as exc:
        raise _http_error(exc) from exc
    task = create_snapshot_task.delay(
        categories=payload.categories,
        incremental=payload.incremental,
        label=payload.label,
        author_type="user",
        author_id=str(current_user.id),
        admin_user_id=current_user.id,
    )
    return {"celery_task_id": task.id, "status": "queued", "message": "Snapshot job created."}


@router.post("/snapshots/import", status_code=status.HTTP_201_CREATED)
async def import_snapshot(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    if not file.filename or not file.filename.endswith(".tar"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Expected an uncompressed .tar snapshot export")
    service = SnapshotService()
    with tempfile.NamedTemporaryFile(dir=service.root, prefix=".upload-", suffix=".tar") as upload:
        while chunk := await file.read(1024 * 1024):
            upload.write(chunk)
        upload.flush()
        try:
            with service.exclusive():
                return service.import_tar(Path(upload.name))
        except SnapshotError as exc:
            raise _http_error(exc) from exc


@router.get("/snapshots/download-signed")
async def download_snapshot_signed(expires: int, user_id: str, sig: str, snapshot_id: str) -> StreamingResponse:
    if datetime.now(timezone.utc).timestamp() > expires:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Signed URL expired")
    if not hmac.compare_digest(sig, _build_download_signature(snapshot_id, expires, user_id)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signed URL")
    return _stream(snapshot_id)


def _stream(snapshot_id: str) -> StreamingResponse:
    service = SnapshotService()
    try:
        service.snapshot_path(snapshot_id)
    except SnapshotError as exc:
        raise _http_error(exc) from exc
    return StreamingResponse(
        service.iter_tar(snapshot_id),
        media_type="application/x-tar",
        headers={"Content-Disposition": f'attachment; filename="{snapshot_id}.tar"'},
    )


@router.get("/snapshots/{snapshot_id}/download")
async def download_snapshot(snapshot_id: str, current_user: User = Depends(get_current_admin_user)) -> StreamingResponse:
    return _stream(snapshot_id)


@router.post("/snapshots/{snapshot_id}/download-link")
async def create_snapshot_download_link(snapshot_id: str, current_user: User = Depends(get_current_admin_user)) -> dict[str, Any]:
    try:
        SnapshotService().snapshot_path(snapshot_id)
    except SnapshotError as exc:
        raise _http_error(exc) from exc
    expires_at = int((datetime.now(timezone.utc) + timedelta(seconds=SIGNED_DOWNLOAD_TTL_SECONDS)).timestamp())
    user_id = str(current_user.id)
    query = urlencode(
        {
            "snapshot_id": snapshot_id,
            "expires": str(expires_at),
            "user_id": user_id,
            "sig": _build_download_signature(snapshot_id, expires_at, user_id),
        }
    )
    return {
        "url": f"/backups/snapshots/download-signed?{query}",
        "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
        "ttl_seconds": SIGNED_DOWNLOAD_TTL_SECONDS,
    }


@router.get("/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str, current_user: User = Depends(get_current_admin_user)) -> dict[str, Any]:
    service = SnapshotService()
    try:
        return service._summary(service.read_manifest(snapshot_id))
    except SnapshotError as exc:
        raise _http_error(exc) from exc


@router.delete("/snapshots/{snapshot_id}")
async def delete_snapshot(snapshot_id: str, current_user: User = Depends(get_current_admin_user)) -> dict[str, Any]:
    try:
        return SnapshotService().delete_snapshot(snapshot_id)
    except SnapshotError as exc:
        raise _http_error(exc) from exc


@router.post("/snapshots/{snapshot_id}/restore", status_code=status.HTTP_202_ACCEPTED)
async def restore_snapshot(
    snapshot_id: str,
    payload: SnapshotRestoreRequest,
    current_user: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    """Destructive: replaces the live files of the selected categories with the snapshot's."""
    service = SnapshotService()
    try:
        manifest = service.read_manifest(snapshot_id)
        missing = [item for item in payload.categories if item not in manifest["categories"]]
        if missing:
            raise SnapshotError(f"Snapshot does not contain: {', '.join(missing)}")
        service._resolve_selection(payload.categories)
    except SnapshotError as exc:
        raise _http_error(exc) from exc
    task = restore_snapshot_task.delay(
        snapshot_id=snapshot_id,
        categories=payload.categories,
        safety_snapshot=payload.safety_snapshot,
        keep_only_admin_in_rpg=payload.keep_only_admin_in_rpg,
        author_type="user",
        author_id=str(current_user.id),
        admin_user_id=current_user.id,
    )
    return {"celery_task_id": task.id, "status": "queued", "message": "Restore job created."}
