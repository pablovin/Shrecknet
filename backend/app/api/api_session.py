from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.database import get_session
from app.models.model_user import User
from app.dependencies import get_current_user
from app.schemas.schema_session import SessionCreate, SessionRead
from app.schemas.schema_session_poll import (
    SessionPollCreate,
    SessionPollRead,
    SessionPollVoteCreate,
)
from app.crud.crud_session import create_session, get_sessions_for_table
from app.crud.crud_session_poll import (
    create_poll,
    get_poll,
    cast_vote,
    finalize_poll,
    poll_to_read,
)

router = APIRouter(prefix="/tables", tags=["sessions"], dependencies=[Depends(get_current_user)])

@router.post("/{table_id}/sessions", response_model=SessionRead)
async def create_session_endpoint(
    table_id: int,
    session_in: SessionCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    session_in.table_id = table_id
    sess = await create_session(session, session_in, user.id)
    return SessionRead(
        id=sess.id,
        table_id=sess.table_id,
        name=sess.name,
        scheduled_time=sess.scheduled_time,
        summary=sess.summary,
        location=sess.location,
        created_by=sess.created_by,
        created_at=sess.created_at,
        page_ids=[p.page_id for p in sess.pages],
    )

@router.get("/{table_id}/sessions", response_model=List[SessionRead])
async def list_sessions_endpoint(
    table_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    sessions = await get_sessions_for_table(session, table_id)
    return [
        SessionRead(
            id=s.id,
            table_id=s.table_id,
            name=s.name,
            scheduled_time=s.scheduled_time,
            summary=s.summary,
            location=s.location,
            created_by=s.created_by,
            created_at=s.created_at,
            page_ids=[p.page_id for p in s.pages],
        )
        for s in sessions
    ]


@router.post("/{table_id}/sessions/{session_id}/poll", response_model=SessionPollRead)
async def create_poll_endpoint(
    table_id: int,
    session_id: int,
    poll_in: SessionPollCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    poll = await create_poll(session, session_id, poll_in)
    return await poll_to_read(session, poll)


@router.get("/{table_id}/sessions/{session_id}/poll", response_model=SessionPollRead)
async def get_poll_endpoint(
    table_id: int,
    session_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    poll = await get_poll(session, session_id)
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    return await poll_to_read(session, poll)


@router.post("/{table_id}/sessions/{session_id}/poll/vote", response_model=SessionPollRead)
async def vote_poll_endpoint(
    table_id: int,
    session_id: int,
    vote: SessionPollVoteCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    poll = await get_poll(session, session_id)
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    await cast_vote(session, poll, user.id, vote)
    return await poll_to_read(session, poll)


@router.post("/{table_id}/sessions/{session_id}/poll/finalize", response_model=SessionPollRead)
async def finalize_poll_endpoint(
    table_id: int,
    session_id: int,
    vote: SessionPollVoteCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    poll = await get_poll(session, session_id)
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    poll = await finalize_poll(session, poll, vote.option_id)
    return await poll_to_read(session, poll)
