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
from app.services import google_workspace_config
from app.models.shrecktables import (
    GoogleConnection, GoogleOAuthState, Table, TableSession, TableAttachment,
)

SCOPES = (
    "openid email "
    "https://www.googleapis.com/auth/calendar.app.created "
    "https://www.googleapis.com/auth/calendar.events "
    "https://www.googleapis.com/auth/calendar.acls "
    "https://www.googleapis.com/auth/meetings.space.readonly "
    "https://www.googleapis.com/auth/meetings.space.settings "
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly "
    "https://www.googleapis.com/auth/drive.meet.readonly"
)
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
MEET_BASE = "https://meet.googleapis.com/v2"
DRIVE_BASE = "https://www.googleapis.com/drive/v3"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _settings():
    s, _ = google_workspace_config.resolve(get_settings())
    if not google_workspace_config.configured(s):
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


def calendar_choices(connection: GoogleConnection, page_token: str | None = None) -> dict:
    token = access_token(connection)
    params = {"maxResults": 250, "showHidden": True}
    if page_token:
        params["pageToken"] = page_token
    page = google("GET", CALENDAR_BASE + "/users/me/calendarList", token, params=params)
    return {"results": [
        {"id": item["id"], "name": item.get("summaryOverride") or item.get("summary") or item["id"],
         "timezone": item.get("timeZone"), "access_role": item["accessRole"]}
        for item in page.get("items", []) if item.get("id") and not item.get("primary")
        and not item.get("deleted") and item.get("accessRole") in {"owner", "writer"}
    ], "next_page_token": page.get("nextPageToken")}


def calendar_events(db: Session, table: Table, connection: GoogleConnection,
                    start: datetime, end: datetime, page_token: str | None = None,
                    *, organizer: bool = True) -> dict:
    token = access_token(connection)
    if not organizer:
        metadata = google("GET", CALENDAR_BASE + "/users/me/calendarList/" + quote(table.calendar_id, safe=""), token)
        if metadata.get("primary"):
            raise HTTPException(403, {"code": "google_calendar_permissions"})
    params = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": True,
              "showDeleted": False, "orderBy": "startTime", "maxResults": 250,
              "timeZone": table.timezone}
    if page_token:
        params["pageToken"] = page_token
    page = google("GET", CALENDAR_BASE + "/calendars/" + quote(table.calendar_id, safe="") + "/events",
                  token, params=params)
    session_ids = {row.google_event_id: row.id for row in db.scalars(select(TableSession).where(
        TableSession.table_id == table.id, TableSession.google_event_id.is_not(None),
        (TableSession.google_event_calendar_id == table.calendar_id) | TableSession.google_event_calendar_id.is_(None)
    )).all()}
    results = []
    for item in page.get("items", []):
        if item.get("status") == "cancelled" or not item.get("id"):
            continue
        begins, ends = item.get("start") or {}, item.get("end") or {}
        first, last = begins.get("dateTime") or begins.get("date"), ends.get("dateTime") or ends.get("date")
        if not first or not last:
            continue
        private = item.get("visibility") in {"private", "confidential"}
        results.append({"id": item["id"], "title": "Private event" if private else item.get("summary") or "Untitled event",
                        "start": first, "end": last, "all_day": bool(begins.get("date")),
                        "session_id": session_ids.get(item["id"])})
    return {"results": results, "next_page_token": page.get("nextPageToken")}


def unlink_calendar(db: Session, table: Table) -> None:
    # Preserve historical event and Meet references. Record their original calendar
    # before removing the table mapping so future sync cannot patch the wrong event.
    for row in db.scalars(select(TableSession).where(TableSession.table_id == table.id)).all():
        if row.google_event_id or row.meet_space:
            if not row.google_event_calendar_id:
                row.google_event_calendar_id = table.calendar_id
                row.google_event_connection_id = table.google_connection_id
            row.calendar_sync_status = "detached"
        else:
            row.calendar_sync_status = "not_configured"
        row.calendar_sync_error = None
    table.calendar_id = None
    table.google_connection_id = None
    table.revision += 1
    db.commit()


def require_calendar_sharing_scope(connection: GoogleConnection) -> None:
    scopes = set((connection.scopes or "").split())
    if not scopes.intersection({"https://www.googleapis.com/auth/calendar.acls",
                                "https://www.googleapis.com/auth/calendar"}):
        raise HTTPException(409, {"code": "google_calendar_sharing_reauthorization"})


def ensure_public_calendar(calendar_id: str, token: str) -> None:
    """Set the public ACL to read-only and verify Google's returned rule."""
    url = CALENDAR_BASE + "/calendars/" + quote(calendar_id, safe="") + "/acl"
    body = {"scope": {"type": "default"}, "role": "reader"}
    try:
        google("POST", url, token, body=body, params={"sendNotifications": False})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise
        google("PUT", url + "/default", token, body=body)
    actual = google("GET", url + "/default", token)
    if actual.get("role") != "reader" or (actual.get("scope") or {}).get("type") != "default":
        raise HTTPException(409, {"code": "google_calendar_public_sharing_blocked"})


def make_linked_calendar_public(table: Table, connection: GoogleConnection) -> None:
    """Explicit organizer action only; never change externally linked calendars automatically."""
    require_calendar_sharing_scope(connection)
    token = access_token(connection)
    metadata = google("GET", CALENDAR_BASE + "/users/me/calendarList/" + quote(table.calendar_id, safe=""), token)
    if metadata.get("primary") or metadata.get("accessRole") != "owner":
        raise HTTPException(403, {"code": "google_calendar_public_owner_required"})
    ensure_public_calendar(table.calendar_id, token)


def create_calendar(db: Session, table: Table, connection: GoogleConnection, name: str | None = None) -> str:
    require_calendar_sharing_scope(connection)
    token = access_token(connection)
    record = google("POST", CALENDAR_BASE + "/calendars", token,
        body={"summary": name or "ShreckTables — " + table.name, "timeZone": table.timezone})
    calendar_id = record["id"]
    try:
        ensure_public_calendar(calendar_id, token)
    except Exception:
        # Never attach a private calendar as a successful ShreckTables creation.
        # Only clean up the brand-new, empty calendar created by this request.
        try:
            google("DELETE", CALENDAR_BASE + "/calendars/" + quote(calendar_id, safe=""), token)
        except (httpx.HTTPError, RuntimeError, ValueError):
            pass  # Organizer may need to remove the unattached calendar manually.
        raise
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
    if item.get("primary") and selected != table.calendar_id:
        raise HTTPException(422, {"code": "primary_calendar_not_supported"})
    if item.get("accessRole") not in {"owner", "writer"}:
        raise HTTPException(403, "Google account needs writer access to this calendar")
    table.calendar_id = selected
    table.google_connection_id = connection.id
    table.revision += 1
    db.commit()
    return selected


MEET_POLICY_VERSION = 2


def _meet_link(event: dict) -> str | None:
    candidates = [event.get("hangoutLink")]
    candidates.extend(entry.get("uri") for entry in
                      (event.get("conferenceData") or {}).get("entryPoints", [])
                      if entry.get("entryPointType") == "video")
    return next((uri for uri in candidates if isinstance(uri, str)
                 and re.fullmatch(r"https://meet\\.google\\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}", uri)), None)


def _resolve_meet_space(token: str, meet_uri: str) -> str | None:
    match = re.fullmatch(r"https://meet\\.google\\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})", meet_uri)
    if not match:
        return None
    space = google("GET", MEET_BASE + "/spaces/" + match.group(1), token)
    name = space.get("name")
    return name if isinstance(name, str) and re.fullmatch(r"spaces/[A-Za-z0-9_-]+", name) else None


def _configure_auto_artifacts(token: str, name: str) -> list[str]:
    """Attempt each independent setting; unsupported artifacts must not hide a valid room."""
    failures = []
    settings = (
        ("accessType", "config.accessType", {"accessType": "OPEN"}),
        ("entryPointAccess", "config.entryPointAccess", {"entryPointAccess": "ALL"}),
        ("transcription", "config.artifactConfig.transcriptionConfig.autoTranscriptionGeneration",
         {"artifactConfig": {"transcriptionConfig": {"autoTranscriptionGeneration": "ON"}}}),
        ("smart notes", "config.artifactConfig.smartNotesConfig.autoSmartNotesGeneration",
         {"artifactConfig": {"smartNotesConfig": {"autoSmartNotesGeneration": "ON"}}}),
    )
    for label, mask, config in settings:
        try:
            google("PATCH", MEET_BASE + "/" + name, token, params={"updateMask": mask},
                   body={"name": name, "config": config})
        except httpx.HTTPStatusError as exc:
            failures.append(f"{label} (HTTP {exc.response.status_code})")
        except (httpx.HTTPError, RuntimeError, ValueError):
            failures.append(f"{label} (unavailable)")
    return failures



def sync_event(db: Session, session_id: int, *, allow_calendar_change: bool = False) -> str:
    row = db.get(TableSession, session_id)
    if not row:
        return "missing"
    table = db.get(Table, row.table_id)
    if not table or not table.calendar_id or not table.google_connection_id:
        row.calendar_sync_status = "detached" if row.google_event_calendar_id else "not_configured"
        db.commit()
        return row.calendar_sync_status
    changed_calendar = bool(row.google_event_calendar_id and row.google_event_calendar_id != table.calendar_id)
    if changed_calendar:
        scheduled = row.scheduled_at_utc
        if scheduled and scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        if not allow_calendar_change or row.status != "scheduled" or not scheduled or scheduled <= datetime.now(timezone.utc):
            row.calendar_sync_status = "detached"
            db.commit()
            return "detached"
    connection = db.get(GoogleConnection, table.google_connection_id)
    if not connection or connection.status != "connected":
        row.calendar_sync_status = "failed"
        row.calendar_sync_error = "Google connection unavailable"
        db.commit()
        return "failed"
    try:
        token = access_token(connection)
        if changed_calendar:
            row.google_event_id = None
            row.google_meet_url = None
            row.meet_space = None
            row.meet_policy_version = 0
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
        row.google_event_calendar_id = table.calendar_id
        row.google_event_connection_id = connection.id
        row.calendar_sync_status = "partial"
        row.calendar_sync_error = "Checking Google Meet conference"
        db.commit()
        # A conference creation request is asynchronous. On subsequent syncs
        # retry a failed or missing request on the SAME Calendar event.
        link = _meet_link(event)
        if not link:
            event_url = url + "/" + quote(row.google_event_id, safe="")
            event = google("GET", event_url, token)
            link = _meet_link(event)
            request_state = ((event.get("conferenceData") or {}).get("createRequest") or {}).get("status") or {}
            if not link and request_state.get("statusCode") != "pending":
                # A new request ID is needed after a failed conference request.
                event = google("PATCH", event_url, token, params={"conferenceDataVersion": 1},
                               body={"conferenceData": {"createRequest": {
                                   "requestId": "shrecktables-" + uuid4().hex,
                                   "conferenceSolutionKey": {"type": "hangoutsMeet"},
                               }}})
                link = _meet_link(event)
        row.google_event_calendar_id = table.calendar_id
        row.google_event_connection_id = connection.id
        if link and link != row.google_meet_url:
            row.meet_space = None
            row.meet_policy_version = 0
        row.google_meet_url = link
        row.calendar_sync_status = "partial"
        row.calendar_sync_error = "Google Meet link is not yet available" if not link else "Meet settings pending"
        # Persist event identity and link before optional Meet operations.
        db.commit()
        if link:
            try:
                row.meet_space = _resolve_meet_space(token, link)
                db.commit()
                if not row.meet_space:
                    row.calendar_sync_error = "Meet space could not be resolved for documentation"
                elif row.meet_policy_version < MEET_POLICY_VERSION:
                    failures = _configure_auto_artifacts(token, row.meet_space)
                    if failures:
                        row.calendar_sync_error = ("Meet settings unavailable: " + ", ".join(failures))[:255]
                    else:
                        row.meet_policy_version = MEET_POLICY_VERSION
                        row.calendar_sync_status = "synced"
                        row.calendar_sync_error = None
                else:
                    row.calendar_sync_status = "synced"
                    row.calendar_sync_error = None
            except (httpx.HTTPError, RuntimeError, ValueError, HTTPException):
                row.calendar_sync_status = "partial"
                row.calendar_sync_error = "Meet space or settings unavailable; calendar link retained"
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
    if not table or not table.google_connection_id:
        return {"imported": 0, "state": "disabled"}
    if (row.google_event_calendar_id and row.google_event_calendar_id != table.calendar_id) or row.calendar_sync_status == "detached":
        return {"imported": 0, "state": "detached"}
    if row.google_event_connection_id and row.google_event_connection_id != table.google_connection_id:
        return {"imported": 0, "state": "detached"}
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
        for endpoint, source in (
            ("transcripts", "transcript"),
            ("smartNotes", "gemini_notes")
        ):
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
