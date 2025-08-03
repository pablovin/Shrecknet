from typing import List, Optional, Set

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.model_session import (
    Session,
    SessionAttendance,
    SessionPage,
    SessionPoll,
    SessionPollOption,
    SessionPollVote,
)
from app.models.model_table import TableMember, Table
from app.schemas.schema_session import SessionCreate
from app.crud.crud_news import create_news
from app.schemas.schema_news import NewsCreate


async def create_session(
    session: AsyncSession, session_in: SessionCreate, creator_id: int
) -> Session:
    sess = Session(
        table_id=session_in.table_id,
        name=session_in.name,
        scheduled_time=session_in.scheduled_time,
        summary=session_in.summary,
        location=session_in.location,
        timezone=session_in.timezone,
        created_by=creator_id,
    )
    session.add(sess)
    await session.commit()
    await session.refresh(sess)

    attendee_ids: Set[int] = set(session_in.attendee_ids or [])
    if not attendee_ids:
        result = await session.execute(
            select(TableMember.user_id).where(
                TableMember.table_id == session_in.table_id
            )
        )
        attendee_ids = set(result.scalars().all())

    # Avoid inserting duplicate attendance records which would violate the
    # uniqueness constraint on (session_id, user_id). This can happen if
    # records from a previous run remain in the database or if duplicate
    # attendee IDs are provided.
    if attendee_ids:
        existing_result = await session.execute(
            select(SessionAttendance.user_id).where(
                (SessionAttendance.session_id == sess.id)
                & (SessionAttendance.user_id.in_(attendee_ids))
            )
        )
        existing_ids = set(existing_result.scalars().all())
    else:
        existing_ids = set()

    for uid in attendee_ids - existing_ids:
        session.add(SessionAttendance(session_id=sess.id, user_id=uid, attending=True))

    for pid in session_in.page_ids or []:
        session.add(SessionPage(session_id=sess.id, page_id=pid))

    await session.commit()

    # Ensure related pages are loaded before returning the session. Without
    # explicitly refreshing the relationship SQLAlchemy will attempt a lazy
    # load later which fails under async sessions with a MissingGreenlet
    # error. By refreshing here we eagerly load the relationship while the
    # session is still active.
    await session.refresh(sess, attribute_names=["pages"])
    table = await session.get(Table, session_in.table_id)
    date_info = (
        sess.scheduled_time.isoformat() if sess.scheduled_time else "unscheduled"
    )
    if attendee_ids:
        news = NewsCreate(
            title="Session Created",
            type="gaming_session",
            description=(
                f"Session '{sess.name}' for table '{table.name}' scheduled on {date_info}. "
                f"View: /tables/{table.id}"
            ),
            user_ids=list(attendee_ids),
        )
        await create_news(session, news)
    return sess


async def get_sessions_for_table(
    session: AsyncSession, table_id: int, user_id: int | None = None
) -> List[Session]:
    """Return sessions for a table.

    If ``user_id`` is provided, only sessions where the user has an attendance
    record are returned.
    """

    stmt = (
        select(Session)
        .where(Session.table_id == table_id)
        .options(selectinload(Session.pages))
    )
    if user_id is not None:
        stmt = stmt.join(SessionAttendance).where(SessionAttendance.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_session(session: AsyncSession, session_id: int) -> Optional[Session]:
    result = await session.execute(
        select(Session)
        .where(Session.id == session_id)
        .options(selectinload(Session.pages))
    )
    return result.scalars().first()


async def update_session(
    session: AsyncSession, session_id: int, updates: dict
) -> Optional[Session]:
    sess = await get_session(session, session_id)
    if not sess:
        return None
    page_ids = updates.pop("page_ids", None)
    for k, v in updates.items():
        setattr(sess, k, v)
    if page_ids is not None:
        result = await session.execute(
            select(SessionPage).where(SessionPage.session_id == session_id)
        )
        for sp in result.scalars().all():
            await session.delete(sp)
        for pid in page_ids:
            session.add(SessionPage(session_id=session_id, page_id=pid))
    await session.commit()
    await session.refresh(sess, attribute_names=["pages"])
    return sess


async def delete_session(session: AsyncSession, session_id: int) -> bool:
    sess = await get_session(session, session_id)
    if not sess:
        return False
    # Remove related attendance records
    await session.execute(
        delete(SessionAttendance).where(SessionAttendance.session_id == session_id)
    )
    # Remove related page links
    await session.execute(
        delete(SessionPage).where(SessionPage.session_id == session_id)
    )
    # Clean up poll information if present
    result = await session.execute(
        select(SessionPoll).where(SessionPoll.session_id == session_id)
    )
    poll = result.scalar_one_or_none()
    if poll:
        await session.execute(
            delete(SessionPollVote).where(SessionPollVote.poll_id == poll.id)
        )
        await session.execute(
            delete(SessionPollOption).where(SessionPollOption.poll_id == poll.id)
        )
        await session.delete(poll)
    await session.delete(sess)
    await session.commit()
    return True
