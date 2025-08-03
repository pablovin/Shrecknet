from typing import List, Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.model_session import (
    Session,
    SessionPoll,
    SessionPollOption,
    SessionPollVote,
)
from app.schemas.schema_session_poll import (
    SessionPollCreate,
    SessionPollVoteCreate,
    SessionPollRead,
    SessionPollOptionRead,
)


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
    session.add(
        SessionPollVote(poll_id=poll.id, option_id=vote_in.option_id, user_id=user_id)
    )
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
