from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.page_visit import PageUserVisit, PageVisit, PageVisitStats
from app.repositories.page_visit_repository import PageVisitRepository


class PageVisitService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = PageVisitRepository(session)

    async def record_visit(self, *, page_key: str, user_id: int) -> PageVisit:
        visited_at = datetime.now(timezone.utc)
        visit = await self.repository.create_visit(
            page_key=page_key, user_id=user_id, visited_at=visited_at
        )
        await self.session.flush()

        user_page_visit = await self.repository.get_user_page_visit(
            page_key=page_key, user_id=user_id
        )
        is_new_user = user_page_visit is None
        if user_page_visit is None:
            await self.repository.create_user_page_visit(
                page_key=page_key, user_id=user_id, visited_at=visited_at
            )
        else:
            user_page_visit.visit_count += 1
            user_page_visit.last_visited_at = visited_at
            await self.session.flush()

        stats = await self.repository.get_page_stats(page_key)
        if stats is None:
            total_visits = await self.repository.count_page_visits(page_key)
            unique_users = await self.repository.count_page_unique_users(page_key)
            stats = await self.repository.create_page_stats(
                page_key=page_key,
                total_visits=total_visits,
                unique_users=unique_users,
                last_visited_at=visited_at,
            )
        else:
            stats.total_visits += 1
            if is_new_user:
                stats.unique_users += 1
            stats.last_visited_at = visited_at
            await self.session.flush()

        await self.session.commit()
        await self.session.refresh(visit)
        return visit

    async def get_page_stats(self, page_key: str) -> PageVisitStats | None:
        return await self.repository.get_page_stats(page_key)

    async def list_recent_visits(
        self, *, page_key: str, limit: int
    ) -> Sequence[tuple[int, str, datetime]]:
        return await self.repository.list_recent_visits(page_key=page_key, limit=limit)

    async def list_user_visits(
        self,
        *,
        user_id: int,
        skip: int,
        limit: int,
    ) -> Sequence[PageVisit]:
        return await self.repository.list_user_visits(
            user_id=user_id, skip=skip, limit=limit
        )

    async def list_user_page_summaries(
        self,
        *,
        user_id: int,
        skip: int,
        limit: int,
    ) -> Sequence[PageUserVisit]:
        return await self.repository.list_user_page_summaries(
            user_id=user_id, skip=skip, limit=limit
        )
