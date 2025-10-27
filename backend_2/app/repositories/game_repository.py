from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import selectinload

from app.models.game import (
    Game,
    GameSession,
    GameSessionAttendance,
    GameSessionPoll,
    GameSessionPollOption,
    GameSessionPollVote,
)
from app.models.user import User
from app.repositories.base import BaseRepository


class GameRepository(BaseRepository):
    """Data access helpers for games, sessions, and polls."""

    async def list_games(
        self, *, skip: int = 0, limit: int = 50, name: str | None = None,
    ) -> Sequence[Game]:
        query: Select[tuple[Game]] = (
            select(Game)
            .options(
                selectinload(Game.members),
                selectinload(Game.sessions)
                .selectinload(GameSession.attendance)
                .selectinload(GameSessionAttendance.user),
                selectinload(Game.sessions)
                .selectinload(GameSession.polls)
                .selectinload(GameSessionPoll.options)
                .selectinload(GameSessionPollOption.votes),
            )
            .order_by(Game.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        if name:
            query = query.where(Game.name.ilike(f"%{name}%"))
        result = await self.session.execute(query)
        return result.scalars().unique().all()

    async def list_games_for_user(self, user_id: int) -> Sequence[Game]:
        query = (
            select(Game)
            .join(Game.members)
            .where(User.id == user_id)
            .options(
                selectinload(Game.members),
                selectinload(Game.sessions)
                .selectinload(GameSession.attendance)
                .selectinload(GameSessionAttendance.user),
                selectinload(Game.sessions)
                .selectinload(GameSession.polls)
                .selectinload(GameSessionPoll.options)
                .selectinload(GameSessionPollOption.votes),
            )
            .order_by(Game.created_at.desc())
        )
        result = await self.session.execute(query)
        return result.scalars().unique().all()

    async def get_game(self, game_id: int) -> Game | None:
        result = await self.session.execute(
            select(Game)
            .options(
                selectinload(Game.members),
                selectinload(Game.sessions)
                .selectinload(GameSession.attendance)
                .selectinload(GameSessionAttendance.user),
                selectinload(Game.sessions)
                .selectinload(GameSession.polls)
                .selectinload(GameSessionPoll.options)
                .selectinload(GameSessionPollOption.votes),
            )
            .where(Game.id == game_id)
        )
        return result.scalar_one_or_none()

    async def list_sessions_for_game(self, game_id: int) -> Sequence[GameSession]:
        result = await self.session.execute(
            select(GameSession)
            .options(
                selectinload(GameSession.attendance).selectinload(
                    GameSessionAttendance.user
                ),
                selectinload(GameSession.polls)
                .selectinload(GameSessionPoll.options)
                .selectinload(GameSessionPollOption.votes),
                selectinload(GameSession.polls).selectinload(
                    GameSessionPoll.finalized_option
                ),
            )
            .where(GameSession.game_id == game_id)
            .order_by(GameSession.created_at.asc())
        )
        return result.scalars().unique().all()

    async def create_game(self, data: dict[str, Any], members: list[User]) -> Game:
        game = Game(**data)
        game.members = members
        await self.save(game)
        await self.session.refresh(game)
        return game

    async def update_game(self, game: Game, data: dict[str, Any]) -> Game:
        for key, value in data.items():
            setattr(game, key, value)
        await self.save(game)
        await self.session.refresh(game)
        return game

    async def delete_game(self, game: Game) -> None:
        await self.delete(game)

    async def get_session(self, game_id: int, session_id: int) -> GameSession | None:
        result = await self.session.execute(
            select(GameSession)
            .options(
                selectinload(GameSession.attendance).selectinload(
                    GameSessionAttendance.user
                ),
                selectinload(GameSession.polls)
                .selectinload(GameSessionPoll.options)
                .selectinload(GameSessionPollOption.votes),
                selectinload(GameSession.polls).selectinload(
                    GameSessionPoll.finalized_option
                ),
            )
            .where(GameSession.id == session_id, GameSession.game_id == game_id,)
        )
        return result.scalar_one_or_none()

    async def create_session(self, game_id: int, data: dict[str, Any]) -> GameSession:
        session = GameSession(game_id=game_id, **data)
        await self.save(session)
        await self.session.refresh(session)
        return session

    async def update_session(
        self, session: GameSession, data: dict[str, Any]
    ) -> GameSession:
        for key, value in data.items():
            setattr(session, key, value)
        await self.save(session)
        await self.session.refresh(session)
        return session

    async def set_attendance(
        self, session_id: int, user_id: int, attending: bool
    ) -> GameSessionAttendance:
        result = await self.session.execute(
            select(GameSessionAttendance).where(
                GameSessionAttendance.session_id == session_id,
                GameSessionAttendance.user_id == user_id,
            )
        )
        attendance = result.scalar_one_or_none()
        if attendance is None:
            attendance = GameSessionAttendance(
                session_id=session_id, user_id=user_id, attending=attending
            )
            await self.save(attendance)
        else:
            attendance.attending = attending
            await self.save(attendance)
        await self.session.flush()
        return attendance

    async def remove_attendance(self, session_id: int, user_id: int) -> None:
        result = await self.session.execute(
            select(GameSessionAttendance).where(
                GameSessionAttendance.session_id == session_id,
                GameSessionAttendance.user_id == user_id,
            )
        )
        attendance = result.scalar_one_or_none()
        if attendance is not None:
            await self.delete(attendance)

    async def create_poll(
        self, session_id: int, options: list[dict[str, Any]]
    ) -> GameSessionPoll:
        poll = GameSessionPoll(session_id=session_id)
        await self.save(poll)
        await self.session.flush()
        for option_data in options:
            option = GameSessionPollOption(poll_id=poll.id, **option_data)
            await self.save(option)
        await self.session.refresh(poll)
        return poll

    async def get_poll(self, session_id: int, poll_id: int) -> GameSessionPoll | None:
        result = await self.session.execute(
            select(GameSessionPoll)
            .options(
                selectinload(GameSessionPoll.options).selectinload(
                    GameSessionPollOption.votes
                )
            )
            .where(
                GameSessionPoll.id == poll_id, GameSessionPoll.session_id == session_id,
            )
        )
        return result.scalar_one_or_none()

    async def add_poll_option(
        self, poll_id: int, option_data: dict[str, Any]
    ) -> GameSessionPollOption:
        option = GameSessionPollOption(poll_id=poll_id, **option_data)
        await self.save(option)
        await self.session.refresh(option)
        return option

    async def add_vote(self, option_id: int, user_id: int) -> GameSessionPollVote:
        vote = GameSessionPollVote(option_id=option_id, user_id=user_id)
        await self.save(vote)
        await self.session.refresh(vote)
        return vote

    async def remove_vote(self, option_id: int, user_id: int) -> None:
        result = await self.session.execute(
            select(GameSessionPollVote).where(
                GameSessionPollVote.option_id == option_id,
                GameSessionPollVote.user_id == user_id,
            )
        )
        vote = result.scalar_one_or_none()
        if vote is not None:
            await self.delete(vote)

    async def votes_for_option(self, option_id: int) -> Sequence[GameSessionPollVote]:
        result = await self.session.execute(
            select(GameSessionPollVote).where(
                GameSessionPollVote.option_id == option_id
            )
        )
        return result.scalars().all()

    async def has_open_poll(self, session_id: int) -> bool:
        result = await self.session.execute(
            select(func.count())
            .select_from(GameSessionPoll)
            .where(
                GameSessionPoll.session_id == session_id,
                GameSessionPoll.is_finalized.is_(False),
            )
        )
        return result.scalar_one() > 0

    async def finalize_poll(
        self, poll: GameSessionPoll, option: GameSessionPollOption
    ) -> GameSessionPoll:
        poll.is_finalized = True
        poll.finalized_option_id = option.id
        await self.save(poll)
        await self.session.refresh(poll)
        return poll
