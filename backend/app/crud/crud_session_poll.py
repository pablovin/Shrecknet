from typing import List, Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.model_session import (
    Session,
    SessionPoll,
    SessionPollOption,
    SessionPollVote,
)
from app.models.model_table import TableMember, Table
from app.schemas.schema_session_poll import (
    SessionPollCreate,
    SessionPollVoteCreate,
    SessionPollRead,
    SessionPollOptionRead,
)
from app.crud.crud_news import create_news
from app.schemas.schema_news import NewsCreate


async def create_poll(
    session: AsyncSession, session_id: int, poll_in: SessionPollCreate
) -> SessionPoll:
    poll = SessionPoll(session_id=session_id)
    session.add(poll)
    await session.commit()
    await session.refresh(poll)

    for dt in poll_in.proposed_times:
        session.add(SessionPollOption(poll_id=poll.id, proposed_time=dt))
    await session.commit()
    await session.refresh(poll)
    sess = await session.get(Session, session_id)
    table = await session.get(Table, sess.table_id) if sess else None
    if sess and table:
        result = await session.execute(
            select(TableMember.user_id).where(TableMember.table_id == table.id)
        )
        member_ids = list(result.scalars().all())
        if member_ids:
            news = NewsCreate(
                title="Poll Created",
                type="gaming_session",
                description=(
                    f"Poll created for session '{sess.name}' in table '{table.name}'. "
                    f"Please vote! View: /tables/{table.id}"
                ),
                user_ids=member_ids,
            )
            await create_news(session, news)
    return poll


async def get_poll(session: AsyncSession, session_id: int) -> Optional[SessionPoll]:
    result = await session.execute(
        select(SessionPoll).where(SessionPoll.session_id == session_id)
    )
    return result.scalar_one_or_none()


async def cast_vote(
    session: AsyncSession,
    poll: SessionPoll,
    user_id: int,
    vote_in: SessionPollVoteCreate,
) -> None:
    await session.execute(
        delete(SessionPollVote).where(
            (SessionPollVote.poll_id == poll.id) & (SessionPollVote.user_id == user_id)
        )
    )
    for oid in vote_in.option_ids:
        session.add(SessionPollVote(poll_id=poll.id, option_id=oid, user_id=user_id))
    await session.commit()


async def finalize_poll(
    session: AsyncSession, poll: SessionPoll, option_id: int
) -> SessionPoll:
    poll.final_option_id = option_id
    option = await session.get(SessionPollOption, option_id)
    sess = await session.get(Session, poll.session_id)
    if option and sess:
        sess.scheduled_time = option.proposed_time
    await session.commit()
    await session.refresh(poll)
    return poll


async def poll_to_read(session: AsyncSession, poll: SessionPoll) -> SessionPollRead:
    result = await session.execute(
        select(SessionPollOption).where(SessionPollOption.poll_id == poll.id)
    )
    options = result.scalars().all()
    options_read: List[SessionPollOptionRead] = []
    for option in options:
        vote_result = await session.execute(
            select(SessionPollVote.user_id).where(
                SessionPollVote.option_id == option.id
            )
        )
        votes = list(vote_result.scalars().all())
        options_read.append(
            SessionPollOptionRead(
                id=option.id, proposed_time=option.proposed_time, votes=votes
            )
        )
    return SessionPollRead(
        id=poll.id,
        session_id=poll.session_id,
        final_option_id=poll.final_option_id,
        options=options_read,
        created_at=poll.created_at,
    )
