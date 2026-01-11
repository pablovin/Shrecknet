from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import (
    Game,
    GameSession,
    GameSessionAttendance,
    GameSessionPoll,
    GameSessionPollOption,
)
from app.models.ontology import Ontology
from app.models.user import User
from app.repositories.game_repository import GameRepository


class GameService:
    """Business logic for games, sessions, polls, and attendance."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = GameRepository(session)

    # Games -----------------------------------------------------------------
    async def create_game(
        self,
        data: dict,
        member_ids: list[int],
    ) -> Game:
        await self._assert_ontology_exists(data["ontology_id"])
        members = await self._fetch_users(member_ids)
        game = await self.repository.create_game(data, members)
        await self.session.commit()
        reloaded = await self.repository.get_game(game.id)
        return reloaded or game

    async def list_games(
        self, *, skip: int = 0, limit: int = 50, name: str | None = None
    ) -> Sequence[Game]:
        return await self.repository.list_games(skip=skip, limit=limit, name=name)

    async def list_games_for_user(self, user_id: int) -> Sequence[Game]:
        return await self.repository.list_games_for_user(user_id)

    async def get_game(self, game_id: int) -> Game | None:
        return await self.repository.get_game(game_id)

    async def update_game(
        self, game: Game, data: dict, member_ids: list[int] | None = None
    ) -> Game:
        if "ontology_id" in data:
            await self._assert_ontology_exists(data["ontology_id"])
        updated = await self.repository.update_game(game, data)
        if member_ids is not None:
            members = await self._fetch_users(member_ids)
            updated.members = members
            await self.repository.save(updated)
        await self.session.commit()
        return updated

    async def delete_game(self, game: Game) -> None:
        await self.repository.delete_game(game)
        await self.session.commit()

    async def add_members(self, game: Game, member_ids: list[int]) -> Game:
        members = await self._fetch_users(member_ids)
        existing_ids = {member.id for member in game.members}
        for member in members:
            if member.id not in existing_ids:
                game.members.append(member)
        await self.repository.save(game)
        await self.session.commit()
        await self.session.refresh(game)
        return game

    async def remove_member(self, game: Game, user_id: int) -> Game:
        game.members = [member for member in game.members if member.id != user_id]
        await self.repository.save(game)
        await self.session.commit()
        await self.session.refresh(game)
        return game

    # Sessions ---------------------------------------------------------------
    async def create_session(
        self,
        game: Game,
        data: dict,
    ) -> GameSession:
        session_obj = await self.repository.create_session(game.id, data)
        await self.session.commit()
        reloaded = await self.repository.get_session(game.id, session_obj.id)
        return reloaded or session_obj

    async def bulk_create_sessions(
        self,
        game: Game,
        *,
        title_prefix: str,
        dates: Sequence[datetime],
        scheduled_timezone: str | None = None,
        location: str | None = None,
        summary: str | None = None,
        start_index: int = 1,
    ) -> Sequence[GameSession]:
        payloads: list[dict] = []
        for offset, scheduled_date in enumerate(dates, start=start_index):
            payloads.append(
                {
                    "title": f"{title_prefix} {offset}",
                    "scheduled_date": scheduled_date,
                    "scheduled_timezone": scheduled_timezone,
                    "location": location,
                    "summary": summary,
                }
            )
        sessions = await self.repository.create_sessions(game.id, payloads)
        await self.session.commit()
        return await self.repository.get_sessions_by_ids(
            game.id, [session.id for session in sessions]
        )

    async def get_session(self, game_id: int, session_id: int) -> GameSession | None:
        return await self.repository.get_session(game_id, session_id)

    async def list_sessions_for_game(self, game_id: int) -> Sequence[GameSession]:
        return await self.repository.list_sessions_for_game(game_id)

    async def update_session(self, session: GameSession, data: dict) -> GameSession:
        updated = await self.repository.update_session(session, data)
        await self.session.commit()
        return updated

    async def set_attendance(
        self, session: GameSession, user_id: int, attending: bool
    ) -> GameSessionAttendance:
        attendance = await self.repository.set_attendance(
            session.id, user_id, attending
        )
        await self.session.commit()
        return attendance

    async def clear_attendance(self, session: GameSession, user_id: int) -> None:
        await self.repository.remove_attendance(session.id, user_id)
        await self.session.commit()

    async def delete_session(self, session: GameSession) -> None:
        await self.repository.delete_session(session)
        await self.session.commit()

    # Polls -----------------------------------------------------------------
    async def create_poll(
        self,
        session: GameSession,
        options: list[dict],
    ) -> GameSessionPoll:
        if await self.repository.has_open_poll(session.id):
            raise ValueError("An open poll already exists for this session")
        poll = await self.repository.create_poll(session.id, options)
        await self.session.commit()
        await self.session.refresh(poll)
        return poll

    async def add_poll_option(
        self, poll: GameSessionPoll, option_data: dict
    ) -> GameSessionPollOption:
        option = await self.repository.add_poll_option(poll.id, option_data)
        await self.session.commit()
        return option

    async def vote_poll_option(
        self, poll: GameSessionPoll, option: GameSessionPollOption, user_id: int
    ) -> None:
        await self.repository.add_vote(option.id, user_id)
        await self.session.commit()

    async def unvote_poll_option(
        self, option: GameSessionPollOption, user_id: int
    ) -> None:
        await self.repository.remove_vote(option.id, user_id)
        await self.session.commit()

    async def finalize_poll(
        self, session: GameSession, poll: GameSessionPoll, option: GameSessionPollOption
    ) -> tuple[GameSessionPoll, list[int]]:
        poll = await self.repository.finalize_poll(poll, option)
        session.scheduled_date = option.proposed_start
        await self.repository.save(session)

        votes = await self.repository.votes_for_option(option.id)
        attendee_ids: list[int] = []
        for vote in votes:
            await self.repository.set_attendance(session.id, vote.user_id, True)
            attendee_ids.append(vote.user_id)

        await self.session.commit()
        await self.session.refresh(poll)
        await self.session.refresh(session)
        return poll, attendee_ids

    async def get_poll(self, session_id: int, poll_id: int) -> GameSessionPoll | None:
        return await self.repository.get_poll(session_id, poll_id)

    async def delete_poll(self, poll: GameSessionPoll) -> None:
        await self.repository.delete_poll(poll)
        await self.session.commit()

    # Helpers ----------------------------------------------------------------
    async def _assert_ontology_exists(self, ontology_id: int) -> None:
        result = await self.session.execute(
            select(Ontology.id).where(Ontology.id == ontology_id)
        )
        if result.scalar_one_or_none() is None:
            raise ValueError("Ontology not found")

    async def _fetch_users(self, user_ids: Sequence[int]) -> list[User]:
        if not user_ids:
            return []
        result = await self.session.execute(select(User).where(User.id.in_(user_ids)))
        users = result.scalars().all()
        missing = set(user_ids) - {user.id for user in users}
        if missing:
            raise ValueError(f"Users not found: {sorted(missing)}")
        return list(users)
