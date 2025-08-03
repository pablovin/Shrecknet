from typing import List, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.model_session import Session, SessionAttendance, SessionPage
from app.models.model_table import TableMember
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
    for uid in attendee_ids:
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

    if sess.scheduled_time:
        news = NewsCreate(
            title="Session Scheduled",
            type="session",
            description=f"Session for table {session_in.table_id} on {sess.scheduled_time.isoformat()}",
        )
        await create_news(session, news)
    return sess


async def get_sessions_for_table(session: AsyncSession, table_id: int) -> List[Session]:
    result = await session.execute(
        select(Session)
        .where(Session.table_id == table_id)
        .options(selectinload(Session.pages))
    )
    return result.scalars().all()
