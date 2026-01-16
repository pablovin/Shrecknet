from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import Settings, get_settings
from app.models.game import GameSession

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


@dataclass(frozen=True)
class CalendarEventResult:
    event_id: str
    meet_link: str | None


class GoogleCalendarClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._service = self._build_service()

    def _build_service(self):
        credentials = _load_service_account_credentials(
            self._settings.google_service_account_json,
            delegated_user_email=self._settings.google_delegated_user_email,
        )
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def upsert_event(
        self,
        *,
        calendar_id: str,
        session: GameSession,
        timezone_name: str | None,
        event_id: str | None = None,
        default_duration_minutes: int = 180,
    ) -> CalendarEventResult:
        body = _build_event_body(
            session=session,
            timezone_name=timezone_name,
            default_duration_minutes=default_duration_minutes,
        )

        if event_id:
            existing = self._service.events().get(
                calendarId=calendar_id, eventId=event_id
            ).execute()
            if not existing.get("conferenceData"):
                body["conferenceData"] = _build_conference_request()
            event = _execute_event_update_with_fallback(
                self._service,
                calendar_id=calendar_id,
                event_id=event_id,
                body=body,
            )
        else:
            body["conferenceData"] = _build_conference_request()
            event = _execute_event_insert_with_fallback(
                self._service,
                calendar_id=calendar_id,
                body=body,
            )

        return CalendarEventResult(
            event_id=event["id"],
            meet_link=_extract_meet_link(event),
        )

    def delete_event(self, *, calendar_id: str, event_id: str) -> None:
        self._service.events().delete(
            calendarId=calendar_id, eventId=event_id
        ).execute()


def get_google_calendar_client(settings: Settings | None = None) -> GoogleCalendarClient | None:
    settings = settings or get_settings()
    if not settings.google_service_account_json:
        return None
    return GoogleCalendarClient(settings)


def _load_service_account_credentials(
    service_account_json: str | None,
    delegated_user_email: str | None = None,
) -> service_account.Credentials:
    if not service_account_json:
        raise ValueError("Google service account JSON is not configured")
    try:
        payload = json.loads(service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            payload, scopes=SCOPES
        )
        return _apply_delegation(credentials, delegated_user_email)
    except json.JSONDecodeError:
        credentials = service_account.Credentials.from_service_account_file(
            service_account_json, scopes=SCOPES
        )
        return _apply_delegation(credentials, delegated_user_email)


def _apply_delegation(
    credentials: service_account.Credentials,
    delegated_user_email: str | None,
) -> service_account.Credentials:
    if delegated_user_email:
        return credentials.with_subject(delegated_user_email)
    return credentials


def _build_event_body(
    *,
    session: GameSession,
    timezone_name: str | None,
    default_duration_minutes: int,
) -> dict[str, Any]:
    if session.scheduled_date is None:
        raise ValueError("Session has no scheduled_date to sync")

    start = session.scheduled_date
    if timezone_name:
        tz = ZoneInfo(timezone_name)
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz)
        start = start.astimezone(tz)
        time_zone = timezone_name
    else:
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo("UTC"))
            time_zone = "UTC"
        else:
            time_zone = (
                start.tzinfo.key
                if hasattr(start.tzinfo, "key")
                else "UTC"
            )

    end = start + timedelta(minutes=default_duration_minutes)

    return {
        "summary": session.title,
        "description": session.summary or "",
        "location": session.location or "",
        "start": {"dateTime": start.isoformat(), "timeZone": time_zone},
        "end": {"dateTime": end.isoformat(), "timeZone": time_zone},
    }


def _build_conference_request() -> dict[str, Any]:
    return {
        "createRequest": {
            "requestId": uuid.uuid4().hex,
            "conferenceSolutionKey": {"type": "hangoutsMeet"},
        }
    }


def _extract_meet_link(event: dict[str, Any]) -> str | None:
    if event.get("hangoutLink"):
        return event["hangoutLink"]
    conference = event.get("conferenceData") or {}
    for entry in conference.get("entryPoints", []) or []:
        if entry.get("entryPointType") == "video":
            return entry.get("uri")
    return None


def _execute_event_insert_with_fallback(service, *, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return (
            service.events()
            .insert(
                calendarId=calendar_id,
                body=body,
                conferenceDataVersion=1,
            )
            .execute()
        )
    except HttpError as exc:
        if "Invalid conference type value" not in str(exc):
            raise
        body = dict(body)
        body.pop("conferenceData", None)
        return service.events().insert(calendarId=calendar_id, body=body).execute()


def _execute_event_update_with_fallback(
    service,
    *,
    calendar_id: str,
    event_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    try:
        return (
            service.events()
            .update(
                calendarId=calendar_id,
                eventId=event_id,
                body=body,
                conferenceDataVersion=1,
            )
            .execute()
        )
    except HttpError as exc:
        if "Invalid conference type value" not in str(exc):
            raise
        body = dict(body)
        body.pop("conferenceData", None)
        return (
            service.events()
            .update(
                calendarId=calendar_id,
                eventId=event_id,
                body=body,
            )
            .execute()
        )
