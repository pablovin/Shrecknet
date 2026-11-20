"""Google Workspace OAuth, idempotent calendar sync and Meet artifact import.

All Google URLs are fixed API hosts; access tokens and client secrets never reach
the browser. Import is consent-gated per table and persisted as private files.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, quote
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.shrecktables import (
    GoogleConnection, GoogleOAuthState, Table, TableSession, TableAttachment,
)

SCOPES = (
    "openid email "
    "https://www.googleapis.com/auth/calendar.app.created "
    "https://www.googleapis.com/auth/calendar.events "
    "https://www.googleapis.com/auth/meetings.space.readonly "
    "https://www.googleapis.com/auth/meetings.space.settings "
    "https://www.googleapis.com/auth/drive.readonly"
)
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
MEET_BASE = "https://meet.googleapis.com/v2"
DRIVE_BASE = "https://www.googleapis.com/drive/v3"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _settings():
    s = get_settings()
    if not all((s.google_client_id, s.google_client_secret, s.google_redirect_uri, s.google_token_fernet_key)):
        raise HTTPException(503, "Google OAuth is not configured by the administrator")
    return s


def _fernet():
    try:
        return Fernet(_settings().google_token_fernet_key.encode())
    except (ValueError, TypeError) as exc:
        raise HTTPException(503, "Google token encryption is misconfigured") from exc


def oauth_start(db: Session, user_id: str) -> str:
    settings = _settings()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(48)
    db.add(GoogleOAuthState(id=state, owner_user_id=user_id, verifier=verifier,
                            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    db.commit()
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": settings.google_client_id, "redirect_uri": settings.google_redirect_uri,
        "response_type": "code", "access_type": "offline", "prompt": "consent",
        "scope": SCOPES, "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def oauth_finish(db: Session, state: str, code: str) -> GoogleConnection:
    settings = _settings()
    row = db.get(GoogleOAuthState, state)
    if not row:
        raise HTTPException(400, "Invalid or expired Google authorization")
    owner_id, verifier, expiry = row.owner_user_id, row.verifier, row.expires_at
    db.delete(row)
    db.commit()  # One use only, even if token exchange fails.
    expiry = expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)
    if expiry <= datetime.now(timezone.utc):
        raise HTTPException(400, "Google authorization expired")
    with httpx.Client(timeout=15) as client:
        response = client.post(GOOGLE_TOKEN_URL, data={
            "code": code, "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret, "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code", "code_verifier": verifier,
        })
        if response.status_code != 200:
            raise HTTPException(502, "Google authorization failed")
        credentials = response.json()
        token = credentials.get("access_token")
        refresh = credentials.get("refresh_token")
        if not token or not refresh:
            raise HTTPException(409, "Google did not issue offline authorization. Reconnect with consent.")
        who = client.get("https://openidconnect.googleapis.com/v1/userinfo",
                         headers={"Authorization": "Bearer " + token})
        if who.status_code != 200:
            raise HTTPException(502, "Google account identity could not be verified")
        email = who.json().get("email")
        if not email or who.json().get("email_verified") is False:
            raise HTTPException(502, "Google email is not verified")
    existing = db.scalar(select(GoogleConnection).where(
        GoogleConnection.owner_user_id == owner_id,
        GoogleConnection.email == email))
    if existing:
        existing.encrypted_refresh_token = _fernet().encrypt(refresh.encode()).decode()
        existing.scopes = credentials.get("scope", "")
        existing.status = "connected"
        connection = existing
    else:
        connection = GoogleConnection(owner_user_id=owner_id, email=email,
            encrypted_refresh_token=_fernet().encrypt(refresh.encode()).decode(),
            scopes=credentials.get("scope", ""), status="connected")
        db.add(connection)
    db.commit()
    return connection


def access_token(connection: GoogleConnection) -> str:
    s = _settings()
    try:
        refresh = _fernet().decrypt(connection.encrypted_refresh_token.encode()).decode()
    except Exception as exc:
        raise RuntimeError("Encrypted Google connection cannot be decrypted") from exc
    with httpx.Client(timeout=15) as client:
        response = client.post(GOOGLE_TOKEN_URL, data={
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": s.google_client_id, "client_secret": s.google_client_secret,
        })
    if response.status_code != 200:
        raise RuntimeError("Google connection must be reauthorized")
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Google did not return an access token")
    return token


def google(method: str, url: str, token: str, *, params=None, body=None, raw: bool = False):
    # Deliberately never accept a URL sourced from Google Drive / Meet response.
    if not url.startswith((CALENDAR_BASE + "/", MEET_BASE + "/", DRIVE_BASE + "/")):
        raise ValueError("Untrusted Google API URL")
    with httpx.Client(timeout=25, follow_redirects=False) as client:
        response = client.request(method, url, params=params, json=body,
                                  headers={"Authorization": "Bearer " + token})
    response.raise_for_status()
    if raw:
        return response.content
    return response.json() if response.content else {}


def create_calendar(db: Session, table: Table, connection: GoogleConnection) -> str:
    token = access_token(connection)
    record = google("POST", CALENDAR_BASE + "/calendars", token,
        body={"summary": "ShreckTables — " + table.name, "timeZone": table.timezone})
    calendar_id = record["id"]
    table.google_connection_id = connection.id
    table.calendar_id = calendar_id
    table.revision += 1
    db.commit()
    return calendar_id


def link_existing_calendar(db: Session, table: Table, connection: GoogleConnection,
                           calendar_id: str | None = None) -> str:
    """Adopt an existing calendar only after verifying organizer write permissions."""
    selected = calendar_id or table.calendar_id
    if not selected:
        raise HTTPException(422, "Calendar ID required")
    token = access_token(connection)
    try:
        item = google("GET", CALENDAR_BASE + "/users/me/calendarList/" + quote(selected, safe=""), token)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(409, "Connected Google account cannot access this calendar") from exc
    if item.get("accessRole") not in {"owner", "writer"}:
        raise HTTPException(403, "Google account needs writer access to this calendar")
    table.calendar_id = selected
    table.google_connection_id = connection.id
    table.revision += 1
    db.commit()
    return selected


def _configure_auto_artifacts(token: str, meet_uri: str, table: Table) -> str | None:
    match = re.fullmatch(r"https://meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})", meet_uri)
    if not match:
        return None
    # Calendar created the Meet; settings scope is needed for spaces from another app.
    space = google("GET", MEET_BASE + "/spaces/" + match.group(1), token)
    name = space.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"spaces/[A-Za-z0-9_-]+", name):
        return None
    if table.auto_notes or table.auto_transcript:
        artifact = {}
        if table.auto_notes:
            artifact["smartNotesConfig"] = {"autoSmartNotesGeneration": "ON"}
        if table.auto_transcript:
            artifact["transcriptionConfig"] = {"autoTranscriptionGeneration": "ON"}
        google("PATCH", MEET_BASE + "/" + name, token,
               params={"updateMask": "config.artifactConfig"},
               body={"name": name, "config": {"artifactConfig": artifact}})
    return name


def sync_event(db: Session, session_id: int) -> str:
    row = db.get(TableSession, session_id)
    if not row:
        return "missing"
    table = db.get(Table, row.table_id)
    if not table or not table.calendar_id or not table.google_connection_id:
        row.calendar_sync_status = "not_configured"
        db.commit()
        return "not_configured"
    connection = db.get(GoogleConnection, table.google_connection_id)
    if not connection or connection.status != "connected":
        row.calendar_sync_status = "failed"
        row.calendar_sync_error = "Google connection unavailable"
        db.commit()
        return "failed"
    try:
        token = access_token(connection)
        url = CALENDAR_BASE + "/calendars/" + quote(table.calendar_id, safe="") + "/events"
        if row.scheduled_at_utc is None or row.status != "scheduled":
            if row.google_event_id:
                try:
                    google("DELETE", url + "/" + quote(row.google_event_id, safe=""), token)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in {404, 410}:
                        raise
                row.google_event_id = None
            row.calendar_sync_status = "not_configured"
            row.calendar_sync_error = None
            db.commit()
            return "not_configured"
        start = row.scheduled_at_utc.replace(tzinfo=timezone.utc) if row.scheduled_at_utc.tzinfo is None else row.scheduled_at_utc
        end = start + timedelta(minutes=row.duration_minutes)
        body = {
            "summary": row.title, "description": row.summary, "location": row.location or "",
            "start": {"dateTime": start.isoformat(), "timeZone": row.scheduled_timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": row.scheduled_timezone},
        }
        if not row.google_event_id:
            event_id = uuid5(NAMESPACE_URL, "shrecktables:" + str(row.id)).hex
            body["id"] = event_id
            body["conferenceData"] = {"createRequest": {"requestId": "shrecktables-" + event_id,
                "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
            try:
                event = google("POST", url, token, params={"conferenceDataVersion": 1}, body=body)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 409:
                    raise
                event = google("GET", url + "/" + event_id, token)
            row.google_event_id = event_id
        else:
            event = google("PATCH", url + "/" + quote(row.google_event_id, safe=""), token,
                           params={"conferenceDataVersion": 1}, body=body)
        row.google_meet_url = event.get("hangoutLink") or row.google_meet_url
        if not row.google_meet_url:
            conference = event.get("conferenceData") or {}
            for entry in conference.get("entryPoints") or []:
                if entry.get("entryPointType") == "video":
                    row.google_meet_url = entry.get("uri")
                    break
        row.calendar_sync_status = "synced"
        row.calendar_sync_error = None
        if row.google_meet_url:
            try:
                row.meet_space = _configure_auto_artifacts(token, row.google_meet_url, table)
            except (httpx.HTTPError, ValueError):
                row.calendar_sync_status = "partial"
                row.calendar_sync_error = "Meet auto-documentation could not be configured"
        else:
            row.calendar_sync_status = "partial"
            row.calendar_sync_error = "Google Meet link is not yet available"
        db.commit()
        return row.calendar_sync_status
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        db.rollback()
        row = db.get(TableSession, session_id)
        row.calendar_sync_status = "failed"
        row.calendar_sync_error = type(exc).__name__  # Never expose credentials or Google payloads.
        db.commit()
        return "failed"


def _import_document(db: Session, row: TableSession, source: str, document_id: str,
                     original_url: str | None, token: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", document_id):
        raise ValueError("Invalid Google document ID")
    existing = db.scalar(select(TableAttachment).where(
        TableAttachment.session_id == row.id, TableAttachment.source == source,
        TableAttachment.external_id == document_id))
    if existing:
        return False
    data = google("GET", DRIVE_BASE + "/files/" + document_id + "/export", token,
                  params={"mimeType": "text/plain"}, raw=True)
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("Google artifact exceeds document size limit")
    from app.core.config import get_settings
    base = get_settings().media_root / "shrecktables" / str(row.table_id) / str(row.id)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = uuid4().hex + (".notes.txt" if source == "gemini_notes" else ".transcript.txt")
    target = base / name
    temp = base / (name + ".tmp")
    try:
        with temp.open("xb") as f:
            f.write(data)
        os.replace(temp, target)
        db.add(TableAttachment(session_id=row.id, filename="Gemini Notes.txt" if source == "gemini_notes" else "Transcript.txt",
                               media_type="text/plain", storage_path=str(target), byte_count=len(data),
                               source=source, external_id=document_id, original_url=original_url,
                               created_by="google"))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        target.unlink(missing_ok=True)
        return False
    finally:
        temp.unlink(missing_ok=True)


def import_session_artifacts(db: Session, session_id: int) -> dict:
    row = db.get(TableSession, session_id)
    if not row or not row.meet_space:
        return {"imported": 0, "state": "no_meet_space"}
    table = db.get(Table, row.table_id)
    if not table or not table.google_connection_id or not (table.auto_notes or table.auto_transcript):
        return {"imported": 0, "state": "disabled"}
    connection = db.get(GoogleConnection, table.google_connection_id)
    if not connection:
        return {"imported": 0, "state": "not_connected"}
    if not re.fullmatch(r"spaces/[A-Za-z0-9_-]+", row.meet_space):
        raise ValueError("Invalid Meet space")
    token = access_token(connection)
    data = google("GET", MEET_BASE + "/conferenceRecords", token,
                  params={"filter": 'space.name = "' + row.meet_space + '"', "pageSize": 100})
    expected = row.scheduled_at_utc
    if expected and expected.tzinfo is None:
        expected = expected.replace(tzinfo=timezone.utc)
    imported = 0
    for record in data.get("conferenceRecords", []):
        if not record.get("endTime") or not record.get("name", "").startswith("conferenceRecords/"):
            continue
        try:
            started = datetime.fromisoformat(record["startTime"].replace("Z", "+00:00"))
        except (ValueError, KeyError):
            continue
        # Do not attach another conference merely because the Meet code was reused.
        if expected and abs((started - expected).total_seconds()) > 18 * 3600:
            continue
        parent = record["name"]
        for enabled, endpoint, source in (
            (table.auto_transcript, "transcripts", "transcript"),
            (table.auto_notes, "smartNotes", "gemini_notes")
        ):
            if not enabled:
                continue
            if not re.fullmatch(r"conferenceRecords/[A-Za-z0-9_-]+", parent):
                continue
            artifacts = google("GET", MEET_BASE + "/" + parent + "/" + endpoint, token)
            for item in artifacts.get(endpoint, []):
                if item.get("state") != "FILE_GENERATED":
                    continue
                destination = item.get("docsDestination") or {}
                document_id = destination.get("document")
                if not document_id:
                    continue
                imported += int(_import_document(db, row, source, document_id,
                    destination.get("exportUri"), token))
    return {"imported": imported, "state": "ok"}
