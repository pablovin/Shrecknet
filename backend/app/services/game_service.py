from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config_store import get_settings
from app.integrations.google_calendar import get_google_calendar_client
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

logger = logging.getLogger(__name__)


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
        session = reloaded or session_obj
        await self._sync_calendar_for_session(game, session)
        return session

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
        reloaded = await self.repository.get_sessions_by_ids(
            game.id, [session.id for session in sessions]
        )
        for session in reloaded:
            await self._sync_calendar_for_session(game, session)
        return reloaded

    async def get_session(self, game_id: int, session_id: int) -> GameSession | None:
        return await self.repository.get_session(game_id, session_id)

    async def list_sessions_for_game(self, game_id: int) -> Sequence[GameSession]:
        return await self.repository.list_sessions_for_game(game_id)

    async def update_session(self, session: GameSession, data: dict) -> GameSession:
        updated = await self.repository.update_session(session, data)
        await self.session.commit()
        game = await self.repository.get_game(updated.game_id)
        if game:
            await self._sync_calendar_for_session(game, updated)
        reloaded = await self.repository.get_session(updated.game_id, updated.id)
        return reloaded or updated

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
        game = await self.repository.get_game(session.game_id)
        if game:
            await self._delete_calendar_event(game, session)
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
        tz_name: str | None = None
        if option.proposed_start.tzinfo is not None and hasattr(
            option.proposed_start.tzinfo, "key"
        ):
            tz_name = option.proposed_start.tzinfo.key
        if tz_name is None:
            tz_name = session.scheduled_timezone or "UTC"
        session.scheduled_timezone = tz_name
        await self.repository.save(session)

        votes = await self.repository.votes_for_option(option.id)
        attendee_ids: list[int] = []
        for vote in votes:
            await self.repository.set_attendance(session.id, vote.user_id, True)
            attendee_ids.append(vote.user_id)

        await self.session.commit()
        await self.session.refresh(poll)
        await self.session.refresh(session)
        game = await self.repository.get_game(session.game_id)
        if game:
            await self._sync_calendar_for_session(game, session)
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

    async def sync_upcoming_sessions_calendar(self, game: Game) -> dict[str, int]:
        sessions = await self.repository.list_sessions_for_game(game.id)
        now = datetime.now(ZoneInfo("UTC"))
        created = 0
        skipped = 0
        failed = 0
        for session in sessions:
            if session.scheduled_date is None:
                continue
            scheduled = session.scheduled_date
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=ZoneInfo("UTC"))
            if scheduled < now:
                continue
            if session.google_event_id:
                skipped += 1
                continue
            await self._sync_calendar_for_session(game, session)
            if session.google_event_id:
                created += 1
            else:
                failed += 1
        return {
            "created_count": created,
            "skipped_count": skipped,
            "failed_count": failed,
        }

    async def _sync_calendar_for_session(self, game: Game, session: GameSession) -> None:
        if not game.google_calendar_id:
            return
        if session.scheduled_date is None:
            if session.google_event_id:
                await self._delete_calendar_event(game, session)
            return

        client = get_google_calendar_client()
        if client is None:
            return

        settings = get_settings()
        try:
            result = await asyncio.to_thread(
                client.upsert_event,
                calendar_id=game.google_calendar_id,
                session=session,
                timezone_name=session.scheduled_timezone,
                event_id=session.google_event_id,
                default_duration_minutes=settings.google_calendar_default_duration_minutes,
            )
        except Exception:
            logger.exception(
                "Failed to sync Google Calendar event for session %s", session.id
            )
            return
        session.google_event_id = result.event_id
        session.google_meet_link = result.meet_link
        await self.repository.save(session)
        await self.session.commit()

    async def _delete_calendar_event(self, game: Game, session: GameSession) -> None:
        if not (game.google_calendar_id and session.google_event_id):
            return
        client = get_google_calendar_client()
        if client is None:
            return
        try:
            await asyncio.to_thread(
                client.delete_event,
                calendar_id=game.google_calendar_id,
                event_id=session.google_event_id,
            )
        except Exception:
            logger.exception(
                "Failed to delete Google Calendar event for session %s", session.id
            )
            return
        session.google_event_id = None
        session.google_meet_link = None
        await self.repository.save(session)
        await self.session.commit()
