"""ShreckTables Google integration and private session file routes."""
from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ShrecknetPrincipal, get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models.shrecktables import Table, TableSession, TableAttachment, GoogleConnection
from app.api.routers.shrecktables import _table, _session
from app.services import shrecktables_google as service

router = APIRouter(prefix="/api/v1/shrecktables", tags=["ShreckTables Google and documents"])
FILE_TYPES = {
    "text/plain": ".txt", "text/markdown": ".md", "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


@router.get("/google/connections")
def list_connections(actor: ShrecknetPrincipal = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    rows = db.scalars(select(GoogleConnection).where(
        GoogleConnection.owner_user_id == actor.user_id)).all()
    return [{"id": row.id, "email": row.email, "status": row.status,
             "scopes": row.scopes, "created_at": row.created_at} for row in rows]


@router.post("/google/oauth/start")
def start_google(actor: ShrecknetPrincipal = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    return {"authorization_url": service.oauth_start(db, actor.user_id)}


@router.get("/google/oauth/callback")
def google_callback(state: str = Query(min_length=10), code: str = Query(min_length=1),
                    db: Session = Depends(get_db)):
    service.oauth_finish(db, state, code)
    target = get_settings().google_frontend_return_url
    # Fixed administrator-configured URL only, never a user-controlled redirect.
    return RedirectResponse(target, status_code=303) if target else {"status": "connected"}


@router.post("/google/connections/{connection_id}/disconnect")
def disconnect(connection_id: str, actor: ShrecknetPrincipal = Depends(get_current_user),
               db: Session = Depends(get_db)):
    row = db.get(GoogleConnection, connection_id)
    if not row or row.owner_user_id != actor.user_id:
        raise HTTPException(404, "Google connection unavailable")
    row.status = "disconnected"
    row.encrypted_refresh_token = ""
    db.commit()
    return {"status": "disconnected"}


@router.post("/tables/{table_id}/calendar/create")
def table_calendar(table_id: int, connection_id: str,
                   actor: ShrecknetPrincipal = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    table = _table(db, table_id, actor, manage=True)
    if table.calendar_id:
        raise HTTPException(409, "Table already has a calendar")
    connection = db.get(GoogleConnection, connection_id)
    if not connection or connection.status != "connected":
        raise HTTPException(404, "Google connection unavailable")
    if actor.role not in {"admin", "world_builder"} and connection.owner_user_id != actor.user_id:
        raise HTTPException(403, "Connect your own Google account")
    try:
        calendar_id = service.create_calendar(db, table, connection)
    except Exception as exc:
        # Do not leak service account keys or Google user data into API errors.
        raise HTTPException(502, "Google Calendar creation failed") from exc
    return {"calendar_id": calendar_id, "connection_id": connection.id}


@router.post("/tables/{table_id}/sessions/{session_id}/sync-google")
def sync_google(table_id: int, session_id: int, actor: ShrecknetPrincipal = Depends(get_current_user),
                db: Session = Depends(get_db)):
    _table(db, table_id, actor, manage=True)
    _session(db, table_id, session_id)
    result = service.sync_event(db, session_id)
    return {"status": result}


@router.post("/tables/{table_id}/sessions/{session_id}/import-google")
def import_google(table_id: int, session_id: int, actor: ShrecknetPrincipal = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    _table(db, table_id, actor, manage=True)
    _session(db, table_id, session_id)
    try:
        return service.import_session_artifacts(db, session_id)
    except Exception as exc:
        raise HTTPException(502, "Google Meet artifact import failed") from exc


@router.post("/tables/{table_id}/sessions/{session_id}/attachments", status_code=201)
async def upload_document(table_id: int, session_id: int, upload: UploadFile = File(...),
                          actor: ShrecknetPrincipal = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    _table(db, table_id, actor, manage=True)
    _session(db, table_id, session_id)
    if upload.content_type not in FILE_TYPES:
        raise HTTPException(415, "Only PDF, DOCX, Markdown and text are allowed")
    original = Path(upload.filename or "document").name[:255]
    if not original or original in {".", ".."}:
        raise HTTPException(422, "Invalid filename")
    data = await upload.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Attachment exceeds 20 MiB")
    if upload.content_type == "application/pdf" and not data.startswith(b"%PDF-"):
        raise HTTPException(422, "Invalid PDF")
    if upload.content_type and upload.content_type.endswith("document") and not data.startswith(b"PK"):
        raise HTTPException(422, "Invalid DOCX")
    root = get_settings().media_root / "shrecktables" / str(table_id) / str(session_id)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / (uuid4().hex + FILE_TYPES[upload.content_type])
    try:
        with path.open("xb") as f:
            f.write(data)
        row = TableAttachment(session_id=session_id, filename=original, media_type=upload.content_type,
                              byte_count=len(data), storage_path=str(path), created_by=actor.user_id,
                              source="upload")
        db.add(row)
        db.commit()
        return {"id": row.id, "filename": row.filename, "source": row.source, "byte_count": row.byte_count}
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise


@router.get("/tables/{table_id}/sessions/{session_id}/attachments/{attachment_id}")
def download_document(table_id: int, session_id: int, attachment_id: int,
                      actor: ShrecknetPrincipal = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    _table(db, table_id, actor)
    _session(db, table_id, session_id)
    row = db.scalar(select(TableAttachment).where(
        TableAttachment.id == attachment_id, TableAttachment.session_id == session_id))
    if not row:
        raise HTTPException(404, "Attachment not found")
    root = (get_settings().media_root / "shrecktables" / str(table_id) / str(session_id)).resolve()
    file_path = Path(row.storage_path).resolve()
    if not file_path.is_relative_to(root) or not file_path.is_file():
        raise HTTPException(404, "Attachment unavailable")
    return FileResponse(file_path, media_type=row.media_type, filename=row.filename,
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
