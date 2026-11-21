"""ShreckTables authoritative table/session API.

Everything is scoped through membership on the server. Google synchronization is
explicitly asynchronous; a pending event is never represented as synchronized.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, HttpUrl, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import ShrecknetPrincipal, get_current_user, oauth2_scheme
from app.db.session import get_db
from app.integrations.ontologies import shrecknet_read
from app.integrations.worlds import resolve_world
from app.repositories.source_scope import OntologyScope
from app.services.session_narrative import promote_session
from app.services.world_rules import read_world_rules
from app.models.shrecktables import (
    Table, TableMember, TableSession, TablePoll, TablePollOption,
    TablePollVote, TableAttendance, TableAttachment, SessionNarrativeSource,
)

router = APIRouter(prefix="/api/v1/shrecktables", tags=["ShreckTables"])
GLOBAL_MANAGERS = {"admin", "world_builder"}
TABLE_CREATORS = GLOBAL_MANAGERS | {"gm"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise HTTPException(422, "Valid IANA timezone required") from exc


def _utc(value: datetime, zone: str) -> datetime:
    timezone_info = _zone(zone)
    if value.tzinfo is None:
        local = value.replace(tzinfo=timezone_info)
        # Reject nonexistent/ambiguous DST wall times rather than silently moving them.
        if local.astimezone(timezone.utc).astimezone(timezone_info).replace(tzinfo=None) != value:
            raise HTTPException(422, "Local time does not exist in this timezone")
        alternate = local.replace(fold=1)
        if alternate.utcoffset() != local.utcoffset():
            raise HTTPException(422, "Ambiguous local time: supply an explicit UTC offset")
        return local.astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def _instant(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat()


def _table(session: Session, table_id: int, actor: ShrecknetPrincipal, *, manage: bool = False) -> Table:
    row = session.scalar(select(Table).options(selectinload(Table.memberships)).where(Table.id == table_id))
    if row is None:
        raise HTTPException(404, "Table not found")
    if row.status != "active" and actor.role not in GLOBAL_MANAGERS:
        raise HTTPException(410, "Table archived")
    member = next((m for m in row.memberships if m.user_id == actor.user_id), None)
    allowed = actor.role in GLOBAL_MANAGERS or (member is not None and (not manage or member.role == "gm"))
    if not allowed:
        raise HTTPException(403, "Table membership required" if not manage else "Table GM required")
    return row


def _session(session: Session, table_id: int, session_id: int) -> TableSession:
    row = session.scalar(select(TableSession).where(TableSession.id == session_id, TableSession.table_id == table_id))
    if row is None:
        raise HTTPException(404, "Session not found")
    return row


def _poll(session: Session, session_id: int, poll_id: int) -> TablePoll:
    row = session.scalar(select(TablePoll).where(TablePoll.id == poll_id, TablePoll.session_id == session_id))
    if row is None:
        raise HTTPException(404, "Poll not found")
    return row


def _table_read(row: Table, actor: ShrecknetPrincipal, session: Session) -> dict:
    rules = read_world_rules(session, row.world_id)
    members = list(row.memberships)
    upcoming = session.scalar(select(TableSession).where(
        TableSession.table_id == row.id, TableSession.scheduled_at_utc > utcnow(),
        TableSession.status == "scheduled"
    ).order_by(TableSession.scheduled_at_utc, TableSession.id).limit(1))
    next_info = None
    if upcoming:
        attendance = session.scalar(select(TableAttendance).where(
            TableAttendance.session_id == upcoming.id, TableAttendance.user_id == actor.user_id))
        next_info = dict(
            id=upcoming.id, game_id=row.id, title=upcoming.title,
            scheduled_at_utc=_instant(upcoming.scheduled_at_utc),
            attending=attendance.attending if attendance else None,
            confirmed_count=session.scalar(select(func.count()).select_from(TableAttendance).where(
                TableAttendance.session_id == upcoming.id, TableAttendance.attending.is_(True))) or 0
        )
    polls = session.scalars(select(TablePoll).join(TableSession).where(
        TableSession.table_id == row.id, TablePoll.is_finalized.is_(False))).all()
    unanswered = 0
    for poll in polls:
        option_ids = session.scalars(select(TablePollOption.id).where(TablePollOption.poll_id == poll.id)).all()
        if not option_ids or not session.scalar(select(TablePollVote.id).where(
            TablePollVote.option_id.in_(option_ids), TablePollVote.user_id == actor.user_id).limit(1)):
            unanswered += 1
    return dict(
        id=row.id, world_id=row.world_id, ontology_id=row.ontology_id, version_id=rules.version_id,
        rules_conflict=rules.legacy_conflict,
        owner_user_id=int(row.created_by), name=row.name, description=row.description,
        image_url=row.image_url, timezone=row.timezone, vtt=row.vtt_url,
        google_calendar_id=row.calendar_id, member_count=len(members),
        members=[{"user_id": m.user_id, "role": m.role, "character_agent_id": m.character_agent_id} for m in members],
        current_role=next((m.role for m in members if m.user_id == actor.user_id), "admin" if actor.role in GLOBAL_MANAGERS else None),
        next_session=next_info, open_poll_count=len(polls), unanswered_poll_count=unanswered,
        status=row.status, revision=row.revision, auto_notes=row.auto_notes, auto_transcript=row.auto_transcript,
        google_connection_id=row.google_connection_id,
    )


def _poll_read(session: Session, poll: TablePoll) -> dict:
    options = session.scalars(select(TablePollOption).where(TablePollOption.poll_id == poll.id).order_by(TablePollOption.proposed_at_utc)).all()
    return dict(id=poll.id, is_finalized=poll.is_finalized, finalized_option_id=poll.finalized_option_id,
                options=[dict(id=o.id, proposed_start=_instant(o.proposed_at_utc),
                              vote_count=session.scalar(select(func.count()).select_from(TablePollVote).where(TablePollVote.option_id == o.id)) or 0,
                              votes=[{"user_id": int(v.user_id)} for v in session.scalars(select(TablePollVote).where(TablePollVote.option_id == o.id)).all()]) for o in options])


def _session_read(session: Session, row: TableSession) -> dict:
    attendance = session.scalars(select(TableAttendance).where(TableAttendance.session_id == row.id)).all()
    polls = session.scalars(select(TablePoll).where(TablePoll.session_id == row.id)).all()
    narrative = session.get(SessionNarrativeSource, row.id)
    return dict(id=row.id, game_id=row.table_id, table_id=row.table_id,
                narrative_source_id=narrative.source_id if narrative else None,
                title=row.title, description=row.summary, summary=row.summary,
                location=row.location, status=row.status,
                scheduled_at_utc=_instant(row.scheduled_at_utc),
                scheduled_timezone=row.scheduled_timezone, duration_minutes=row.duration_minutes,
                google_meet_link=row.google_meet_url, google_event_id=row.google_event_id,
                calendar_sync_status=row.calendar_sync_status,
                attendance=[dict(user_id=int(a.user_id), attending=a.attending) for a in attendance],
                polls=[_poll_read(session, poll) for poll in polls])


def _enqueue_sync(db: Session, row: TableSession) -> None:
    if row.calendar_sync_status != "pending":
        return
    try:
        from app.tasks.shrecktables import synchronize_session
        synchronize_session.delay(row.id)
    except Exception:
        row.calendar_sync_status = "failed"
        row.calendar_sync_error = "Background calendar worker unavailable"
        db.commit()


class CreateTable(BaseModel):
    world_id: str = Field(min_length=1, max_length=64)
    ontology_id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    image_url: str | None = Field(default=None, max_length=1024)
    timezone: str = "UTC"
    version_id: str | None = None


class UpdateTable(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    image_url: str | None = Field(default=None, max_length=1024)
    timezone: str | None = None
    vtt_url: HttpUrl | None = None
    calendar_id: str | None = Field(default=None, max_length=255)
    google_connection_id: str | None = None
    auto_notes: bool | None = None
    auto_transcript: bool | None = None


class SetMember(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    role: Literal["gm", "player"] = "player"
    character_agent_id: str | None = None


class SessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(default="", max_length=30000)
    scheduled_at: datetime | None = None
    scheduled_timezone: str | None = None
    duration_minutes: int = Field(default=180, ge=15, le=1440)
    location: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def check_zone(self):
        if self.scheduled_at is not None and self.scheduled_at.tzinfo is None and not self.scheduled_timezone:
            raise ValueError("Timezone required for a local timestamp")
        return self


class AttendanceRequest(BaseModel):
    attending: bool


class PollCreate(BaseModel):
    options: list[datetime] = Field(min_length=1, max_length=50)
    timezone: str = "UTC"


class VoteRequest(BaseModel):
    option_id: int


class PollOptionCreate(BaseModel):
    options: list[datetime] = Field(min_length=1, max_length=50)
    timezone: str = "UTC"


class BulkSessionsCreate(BaseModel):
    titles: list[str] = Field(min_length=1, max_length=52)
    scheduled_dates: list[datetime] = Field(min_length=1, max_length=52)
    timezone: str
    duration_minutes: int = Field(default=180, ge=15, le=1440)


class FinalizeRequest(BaseModel):
    option_id: int


@router.get("/tables")
def list_tables(ontology_id: int | None = Query(default=None, gt=0),
                world_id: str | None = Query(default=None, min_length=1, max_length=64),
                actor: ShrecknetPrincipal = Depends(get_current_user),
                session: Session = Depends(get_db)):
    query = select(Table).options(selectinload(Table.memberships)).where(Table.status == "active")
    if ontology_id is not None:
        query = query.where(Table.ontology_id == ontology_id)
    if world_id is not None:
        query = query.where(Table.world_id == world_id)
    if actor.role not in GLOBAL_MANAGERS:
        query = query.where(Table.memberships.any(TableMember.user_id == actor.user_id))
    rows = session.scalars(query.order_by(Table.name, Table.id))
    return [_table_read(row, actor, session) for row in rows]


@router.post("/tables", status_code=201)
def create_table(body: CreateTable, token: str = Depends(oauth2_scheme),
                 actor: ShrecknetPrincipal = Depends(get_current_user),
                 session: Session = Depends(get_db)):
    if actor.role not in TABLE_CREATORS:
        raise HTTPException(403, "GM or Admin required")
    _zone(body.timezone)
    world = resolve_world(body.world_id, token, body.ontology_id)
    rules = read_world_rules(session, world.id)
    if body.version_id is not None and body.version_id != rules.version_id:
        raise HTTPException(409, "Rules are selected for the world")
    row = Table(**body.model_dump(exclude={"ontology_id", "version_id"}),
                ontology_id=world.active_ontology_id, created_by=actor.user_id)
    session.add(row)
    session.flush()
    session.add(TableMember(table_id=row.id, user_id=actor.user_id, role="gm"))
    session.commit()
    return _table_read(_table(session, row.id, actor), actor, session)


@router.get("/tables/{table_id}")
def get_table(table_id: int, actor: ShrecknetPrincipal = Depends(get_current_user),
              session: Session = Depends(get_db)):
    return _table_read(_table(session, table_id, actor), actor, session)


@router.get("/tables/{table_id}/world")
def get_table_world(table_id: int, token: str = Depends(oauth2_scheme),
                    actor: ShrecknetPrincipal = Depends(get_current_user),
                    session: Session = Depends(get_db)):
    table = _table(session, table_id, actor)
    world = resolve_world(table.world_id, token, table.ontology_id)
    rules = read_world_rules(session, world.id)
    return {"world_id": world.id, "name": world.name, "ontology_id": world.active_ontology_id,
            "rules": rules}


@router.patch("/tables/{table_id}")
def patch_table(table_id: int, body: UpdateTable, if_match: int = Header(alias="If-Match"),
                actor: ShrecknetPrincipal = Depends(get_current_user),
                session: Session = Depends(get_db)):
    row = _table(session, table_id, actor, manage=True)
    if row.revision != if_match:
        raise HTTPException(409, "Table revision conflict")
    changes = body.model_dump(exclude_unset=True)
    if "timezone" in changes and changes["timezone"] is not None:
        _zone(changes["timezone"])
    if changes.get("google_connection_id") is not None:
        from app.models.shrecktables import GoogleConnection
        connection = session.get(GoogleConnection, changes["google_connection_id"])
        if not connection or connection.status != "connected":
            raise HTTPException(409, "Google connection is unavailable")
        if actor.role not in GLOBAL_MANAGERS and connection.owner_user_id != actor.user_id:
            raise HTTPException(403, "Cannot use another GM's Google credentials")
    for key, value in changes.items():
        if key == "vtt_url" and value is not None:
            value = str(value)
        setattr(row, key, value)
    row.revision += 1
    session.commit()
    return _table_read(_table(session, table_id, actor), actor, session)


@router.post("/tables/{table_id}/members", status_code=201)
def add_member(table_id: int, body: SetMember, token: str = Depends(oauth2_scheme),
               actor: ShrecknetPrincipal = Depends(get_current_user),
               session: Session = Depends(get_db)):
    row = _table(session, table_id, actor, manage=True)
    user = shrecknet_read("/contracts/users/" + body.user_id, token)
    if not isinstance(user, dict) or str(user.get("id")) != body.user_id:
        raise HTTPException(404, "User not found")
    if session.scalar(select(TableMember).where(TableMember.table_id == row.id, TableMember.user_id == body.user_id)):
        raise HTTPException(409, "Member already exists")
    member = TableMember(table_id=row.id, **body.model_dump())
    session.add(member)
    row.revision += 1
    session.commit()
    return {"user_id": member.user_id, "role": member.role}


@router.patch("/tables/{table_id}/members/{user_id}")
def change_member_role(table_id: int, user_id: str, role: Literal["gm", "player"],
                       actor: ShrecknetPrincipal = Depends(get_current_user),
                       session: Session = Depends(get_db)):
    row = _table(session, table_id, actor, manage=True)
    member = session.scalar(select(TableMember).where(TableMember.table_id == row.id, TableMember.user_id == user_id))
    if not member:
        raise HTTPException(404, "Member not found")
    if member.role == "gm" and role != "gm" and sum(m.role == "gm" for m in row.memberships) <= 1:
        raise HTTPException(409, "Cannot remove the final GM")
    if actor.role != "admin" and user_id == actor.user_id and role != "gm":
        raise HTTPException(409, "Another GM must change your role")
    member.role = role
    row.revision += 1
    session.commit()
    return {"user_id": user_id, "role": role}


@router.delete("/tables/{table_id}/members/{user_id}", status_code=204)
def remove_member(table_id: int, user_id: str, actor: ShrecknetPrincipal = Depends(get_current_user),
                  session: Session = Depends(get_db)):
    row = _table(session, table_id, actor, manage=True)
    member = session.scalar(select(TableMember).where(TableMember.table_id == row.id, TableMember.user_id == user_id))
    if not member:
        raise HTTPException(404, "Member not found")
    if member.role == "gm" and sum(m.role == "gm" for m in row.memberships) <= 1:
        raise HTTPException(409, "Cannot remove the final GM")
    session.delete(member)
    row.revision += 1
    session.commit()


@router.get("/tables/{table_id}/members")
def list_members(table_id: int, token: str = Depends(oauth2_scheme),
                 actor: ShrecknetPrincipal = Depends(get_current_user),
                 session: Session = Depends(get_db)):
    row = _table(session, table_id, actor)
    member_ids = [int(m.user_id) for m in row.memberships]
    profiles = {}
    for start in range(0, len(member_ids), 100):
        batch = member_ids[start:start + 100]
        query = "?" + "&".join("ids=" + str(user_id) for user_id in batch)
        summaries = shrecknet_read("/contracts/users" + query, token)
        if not isinstance(summaries, list):
            raise HTTPException(503, "Member profiles unavailable")
        profiles.update({int(info["id"]): info for info in summaries})
    return [{"id": int(m.user_id), "user_id": m.user_id, "role": m.role,
             "username": profiles.get(int(m.user_id), {}).get("username", m.user_id),
             "full_name": profiles.get(int(m.user_id), {}).get("full_name", m.user_id),
             "avatar_url": profiles.get(int(m.user_id), {}).get("avatar_url")}
            for m in row.memberships]


@router.post("/tables/{table_id}/archive")
def archive_table(table_id: int, actor: ShrecknetPrincipal = Depends(get_current_user),
                  session: Session = Depends(get_db)):
    row = _table(session, table_id, actor, manage=True)
    row.status = "archived"
    row.revision += 1
    session.commit()
    return {"id": row.id, "status": row.status}


@router.get("/tables/{table_id}/sessions")
def list_sessions(table_id: int, view: Literal["upcoming", "past", "unscheduled", "polls", "calendar"] = "upcoming",
                  skip: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100),
                  start: datetime | None = None, end: datetime | None = None,
                  actor: ShrecknetPrincipal = Depends(get_current_user),
                  session: Session = Depends(get_db)):
    _table(session, table_id, actor)
    q = select(TableSession).where(TableSession.table_id == table_id)
    now = utcnow()
    if view == "upcoming":
        q = q.where(TableSession.scheduled_at_utc > now)
    elif view == "past":
        q = q.where(TableSession.scheduled_at_utc <= now)
    elif view == "unscheduled":
        q = q.where(TableSession.scheduled_at_utc.is_(None))
    elif view == "polls":
        q = q.where(TableSession.id.in_(select(TablePoll.session_id).where(TablePoll.is_finalized.is_(False))))
    elif view == "calendar":
        if not start or not end or not start.tzinfo or not end.tzinfo or start >= end or (end - start).days > 93:
            raise HTTPException(422, "Valid timezone-aware calendar range (max 93 days) required")
        q = q.where(TableSession.scheduled_at_utc >= start.astimezone(timezone.utc),
                    TableSession.scheduled_at_utc < end.astimezone(timezone.utc))
    total = session.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0
    q = q.order_by(TableSession.scheduled_at_utc.desc() if view == "past" else TableSession.scheduled_at_utc, TableSession.id)
    rows = session.scalars(q.offset(skip).limit(limit)).all()
    return {"total": total, "skip": skip, "limit": limit, "results": [_session_read(session, r) for r in rows]}


@router.post("/tables/{table_id}/sessions", status_code=201)
def create_session(table_id: int, body: SessionCreate,
                   actor: ShrecknetPrincipal = Depends(get_current_user),
                   session: Session = Depends(get_db)):
    table = _table(session, table_id, actor, manage=True)
    zone = body.scheduled_timezone or table.timezone
    date = _utc(body.scheduled_at, zone) if body.scheduled_at else None
    row = TableSession(table_id=table_id, title=body.title, summary=body.summary,
                       scheduled_at_utc=date, scheduled_timezone=zone,
                       duration_minutes=body.duration_minutes, location=body.location,
                       status="scheduled" if date else "draft", created_by=actor.user_id,
                       calendar_sync_status="pending" if date and table.calendar_id else "not_configured")
    session.add(row)
    session.commit()
    _enqueue_sync(session, row)
    return _session_read(session, row)


@router.post("/tables/{table_id}/sessions/bulk", status_code=201)
def create_bulk_sessions(table_id: int, body: BulkSessionsCreate,
                         actor: ShrecknetPrincipal = Depends(get_current_user),
                         session: Session = Depends(get_db)):
    table = _table(session, table_id, actor, manage=True)
    if len(body.titles) not in {1, len(body.scheduled_dates)}:
        raise HTTPException(422, "Provide one title or one per scheduled date")
    instants = [_utc(value, body.timezone) for value in body.scheduled_dates]
    if len(set(instants)) != len(instants):
        raise HTTPException(422, "Duplicate session dates")
    rows = []
    for i, instant in enumerate(instants):
        name = body.titles[i if len(body.titles) > 1 else 0].strip()
        if not name or len(name) > 255:
            raise HTTPException(422, "Valid session title required")
        row = TableSession(table_id=table_id, title=name, scheduled_at_utc=instant,
                           scheduled_timezone=body.timezone, duration_minutes=body.duration_minutes,
                           status="scheduled", created_by=actor.user_id,
                           calendar_sync_status="pending" if table.calendar_id else "not_configured")
        session.add(row)
        rows.append(row)
    session.commit()
    for row in rows:
        _enqueue_sync(session, row)
    return [_session_read(session, row) for row in rows]


@router.delete("/tables/{table_id}/sessions/{session_id}", status_code=204)
def cancel_session(table_id: int, session_id: int,
                   actor: ShrecknetPrincipal = Depends(get_current_user),
                   session: Session = Depends(get_db)):
    table = _table(session, table_id, actor, manage=True)
    row = _session(session, table_id, session_id)
    # Do not hard-delete sessions with historical Google artifacts, polls or votes.
    row.status = "cancelled"
    row.calendar_sync_status = "pending" if table.calendar_id else "not_configured"
    row.revision += 1
    session.commit()
    _enqueue_sync(session, row)


@router.get("/tables/{table_id}/sessions/{session_id}")
def get_session(table_id: int, session_id: int, actor: ShrecknetPrincipal = Depends(get_current_user),
                session: Session = Depends(get_db)):
    _table(session, table_id, actor)
    return _session_read(session, _session(session, table_id, session_id))


@router.patch("/tables/{table_id}/sessions/{session_id}")
def patch_session(table_id: int, session_id: int, body: SessionCreate,
                  actor: ShrecknetPrincipal = Depends(get_current_user),
                  session: Session = Depends(get_db)):
    table = _table(session, table_id, actor, manage=True)
    row = _session(session, table_id, session_id)
    row.title, row.summary, row.location = body.title, body.summary, body.location
    row.scheduled_timezone = body.scheduled_timezone or table.timezone
    row.scheduled_at_utc = _utc(body.scheduled_at, row.scheduled_timezone) if body.scheduled_at else None
    row.duration_minutes = body.duration_minutes
    row.status = "scheduled" if row.scheduled_at_utc else "draft"
    row.calendar_sync_status = "pending" if table.calendar_id else "not_configured"
    row.revision += 1
    session.commit()
    _enqueue_sync(session, row)
    return _session_read(session, row)


@router.post("/tables/{table_id}/sessions/{session_id}/attendance")
def respond_attendance(table_id: int, session_id: int, body: AttendanceRequest,
                       actor: ShrecknetPrincipal = Depends(get_current_user),
                       session: Session = Depends(get_db)):
    _table(session, table_id, actor)
    row = _session(session, table_id, session_id)
    if row.scheduled_at_utc is None or row.status != "scheduled":
        raise HTTPException(409, "Session is not scheduled")
    response = session.scalar(select(TableAttendance).where(
        TableAttendance.session_id == session_id, TableAttendance.user_id == actor.user_id))
    if not response:
        response = TableAttendance(session_id=session_id, user_id=actor.user_id, attending=body.attending)
        session.add(response)
    response.attending = body.attending
    response.responded_at = utcnow()
    session.commit()
    return _session_read(session, row)


@router.post("/tables/{table_id}/sessions/{session_id}/polls", status_code=201)
def create_poll(table_id: int, session_id: int, body: PollCreate,
                actor: ShrecknetPrincipal = Depends(get_current_user),
                session: Session = Depends(get_db)):
    _table(session, table_id, actor, manage=True)
    _session(session, table_id, session_id)
    if session.scalar(select(TablePoll.id).where(TablePoll.session_id == session_id, TablePoll.is_finalized.is_(False))):
        raise HTTPException(409, "An open poll already exists")
    values = [_utc(v, body.timezone) for v in body.options]
    if len(set(values)) != len(values):
        raise HTTPException(422, "Duplicate poll options")
    poll = TablePoll(session_id=session_id)
    session.add(poll)
    session.flush()
    session.add_all(TablePollOption(poll_id=poll.id, proposed_at_utc=v) for v in values)
    session.commit()
    return _poll_read(session, poll)


@router.post("/tables/{table_id}/sessions/{session_id}/polls/{poll_id}/options")
def add_poll_options(table_id: int, session_id: int, poll_id: int, body: PollOptionCreate,
                     actor: ShrecknetPrincipal = Depends(get_current_user),
                     session: Session = Depends(get_db)):
    _table(session, table_id, actor, manage=True)
    _session(session, table_id, session_id)
    poll = _poll(session, session_id, poll_id)
    if poll.is_finalized:
        raise HTTPException(409, "Poll is finalized")
    existing = set(session.scalars(select(TablePollOption.proposed_at_utc).where(
        TablePollOption.poll_id == poll.id)).all())
    instants = [_utc(v, body.timezone) for v in body.options]
    if len(instants) != len(set(instants)) or any(v in existing for v in instants):
        raise HTTPException(409, "Poll option already exists")
    session.add_all([TablePollOption(poll_id=poll.id, proposed_at_utc=v) for v in instants])
    session.commit()
    return _poll_read(session, poll)


@router.get("/tables/{table_id}/sessions/{session_id}/polls/{poll_id}")
def get_poll(table_id: int, session_id: int, poll_id: int,
             actor: ShrecknetPrincipal = Depends(get_current_user), session: Session = Depends(get_db)):
    _table(session, table_id, actor)
    _session(session, table_id, session_id)
    return _poll_read(session, _poll(session, session_id, poll_id))


@router.post("/tables/{table_id}/sessions/{session_id}/polls/{poll_id}/vote")
def vote(table_id: int, session_id: int, poll_id: int, body: VoteRequest,
         actor: ShrecknetPrincipal = Depends(get_current_user), session: Session = Depends(get_db)):
    _table(session, table_id, actor)
    _session(session, table_id, session_id)
    poll = _poll(session, session_id, poll_id)
    if poll.is_finalized:
        raise HTTPException(409, "Poll is finalized")
    if not session.scalar(select(TablePollOption.id).where(TablePollOption.poll_id == poll.id, TablePollOption.id == body.option_id)):
        raise HTTPException(404, "Option not found")
    existing = session.scalar(select(TablePollVote).where(TablePollVote.option_id == body.option_id, TablePollVote.user_id == actor.user_id))
    if not existing:
        session.add(TablePollVote(option_id=body.option_id, user_id=actor.user_id))
        session.commit()
    return _poll_read(session, poll)


@router.delete("/tables/{table_id}/sessions/{session_id}/polls/{poll_id}/votes/{user_id}/{option_id}")
def unvote(table_id: int, session_id: int, poll_id: int, user_id: int, option_id: int,
           actor: ShrecknetPrincipal = Depends(get_current_user), session: Session = Depends(get_db)):
    _table(session, table_id, actor)
    _session(session, table_id, session_id)
    poll = _poll(session, session_id, poll_id)
    if poll.is_finalized:
        raise HTTPException(409, "Poll is finalized")
    if str(user_id) != actor.user_id:
        raise HTTPException(403, "Only your own votes can be withdrawn")
    if not session.scalar(select(TablePollOption.id).where(TablePollOption.poll_id == poll.id, TablePollOption.id == option_id)):
        raise HTTPException(404, "Option not found")
    session.execute(delete(TablePollVote).where(TablePollVote.option_id == option_id, TablePollVote.user_id == actor.user_id))
    session.commit()
    return _poll_read(session, poll)


@router.post("/tables/{table_id}/sessions/{session_id}/polls/{poll_id}/finalize")
def finalize_poll(table_id: int, session_id: int, poll_id: int, body: FinalizeRequest,
                  actor: ShrecknetPrincipal = Depends(get_current_user),
                  session: Session = Depends(get_db)):
    table = _table(session, table_id, actor, manage=True)
    row = _session(session, table_id, session_id)
    poll = _poll(session, session_id, poll_id)
    if poll.is_finalized:
        raise HTTPException(409, "Poll is finalized")
    choice = session.scalar(select(TablePollOption).where(TablePollOption.poll_id == poll.id, TablePollOption.id == body.option_id))
    if choice is None:
        raise HTTPException(404, "Option not found")
    poll.is_finalized = True
    poll.finalized_option_id = choice.id
    row.scheduled_at_utc = choice.proposed_at_utc
    row.status = "scheduled"
    row.calendar_sync_status = "pending" if table.calendar_id else "not_configured"
    row.revision += 1
    session.commit()
    _enqueue_sync(session, row)
    return _session_read(session, row)


class PromoteNarrative(BaseModel):
    title: str = Field(min_length=1, max_length=255, pattern=r"\S")
    narrative: str = Field(min_length=1, max_length=200000, pattern=r"\S")
    attachment_id: int | None = Field(default=None, gt=0)


@router.post("/tables/{table_id}/sessions/{session_id}/narrative-source", status_code=201)
def promote_narrative(
    table_id: int, session_id: int, body: PromoteNarrative,
    token: str = Depends(oauth2_scheme),
    actor: ShrecknetPrincipal = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    table = _table(session, table_id, actor, manage=True)
    _session(session, table_id, session_id)
    world = resolve_world(table.world_id, token, table.ontology_id)
    promotion = promote_session(
        session, OntologyScope(world.active_ontology_id, world.name, world.description),
        session_id, actor.user_id, body.title, body.narrative, body.attachment_id,
    )
    return {"world_id": world.id, "ontology_id": world.active_ontology_id,
            "source_id": promotion.source_id, "attachment_id": promotion.attachment_id}


@router.get("/tables/{table_id}/sessions/{session_id}/attachments")
def attachments(table_id: int, session_id: int, actor: ShrecknetPrincipal = Depends(get_current_user),
                session: Session = Depends(get_db)):
    _table(session, table_id, actor)
    _session(session, table_id, session_id)
    return [{"id": a.id, "filename": a.filename, "media_type": a.media_type, "byte_count": a.byte_count,
             "source": a.source, "original_url": a.original_url, "created_at": a.created_at}
            for a in session.scalars(select(TableAttachment).where(TableAttachment.session_id == session_id)).all()]
