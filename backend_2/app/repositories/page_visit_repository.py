from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import Select, func, select

from app.models.page_visit import PageUserVisit, PageVisit, PageVisitStats
from app.models.user import User
from app.repositories.base import BaseRepository


class PageVisitRepository(BaseRepository):
    async def create_visit(
        self,
        *,
        page_key: str,
        user_id: int,
        visited_at: datetime | None = None,
    ) -> PageVisit:
        visit = PageVisit(page_key=page_key, user_id=user_id)
        if visited_at is not None:
            visit.visited_at = visited_at
        await self.save(visit)
        return visit

    async def get_page_stats(self, page_key: str) -> PageVisitStats | None:
        result = await self.session.execute(
            select(PageVisitStats).where(PageVisitStats.page_key == page_key)
        )
        return result.scalar_one_or_none()

    async def create_page_stats(
        self,
        *,
        page_key: str,
        total_visits: int,
        unique_users: int,
        last_visited_at: datetime,
    ) -> PageVisitStats:
        stats = PageVisitStats(
            page_key=page_key,
            total_visits=total_visits,
            unique_users=unique_users,
            last_visited_at=last_visited_at,
        )
        await self.save(stats)
        return stats

    async def get_user_page_visit(
        self, *, page_key: str, user_id: int
    ) -> PageUserVisit | None:
        result = await self.session.execute(
            select(PageUserVisit).where(
                PageUserVisit.page_key == page_key,
                PageUserVisit.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_user_page_visit(
        self,
        *,
        page_key: str,
        user_id: int,
        visited_at: datetime,
    ) -> PageUserVisit:
        record = PageUserVisit(
            page_key=page_key,
            user_id=user_id,
            first_visited_at=visited_at,
            last_visited_at=visited_at,
            visit_count=1,
        )
        await self.save(record)
        return record

    async def list_recent_visits(
        self,
        *,
        page_key: str,
        limit: int,
    ) -> Sequence[tuple[int, str, datetime]]:
        query: Select[tuple[int, str, datetime]] = (
            select(PageVisit.user_id, User.username, PageVisit.visited_at)
            .join(User, User.id == PageVisit.user_id)
            .where(PageVisit.page_key == page_key)
            .order_by(PageVisit.visited_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return result.all()

    async def list_user_visits(
        self,
        *,
        user_id: int,
        skip: int,
        limit: int,
    ) -> Sequence[PageVisit]:
        result = await self.session.execute(
            select(PageVisit)
            .where(PageVisit.user_id == user_id)
            .order_by(PageVisit.visited_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def list_user_page_summaries(
        self,
        *,
        user_id: int,
        skip: int,
        limit: int,
    ) -> Sequence[PageUserVisit]:
        result = await self.session.execute(
            select(PageUserVisit)
            .where(PageUserVisit.user_id == user_id)
            .order_by(PageUserVisit.last_visited_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def count_page_unique_users(self, page_key: str) -> int:
        result = await self.session.execute(
            select(func.count(PageUserVisit.id)).where(
                PageUserVisit.page_key == page_key
            )
        )
        return int(result.scalar_one() or 0)

    async def count_page_visits(self, page_key: str) -> int:
        result = await self.session.execute(
            select(func.count(PageVisit.id)).where(PageVisit.page_key == page_key)
        )
        return int(result.scalar_one() or 0)

    async def search_page_keys(
        self,
        *,
        page_key: str | None = None,
        page_alias: str | None = None,
        ontology_instance_id: str | None = None,
    ) -> list[str]:
        """
        Search for page_keys in the database that match the given criteria.
        Performs case-insensitive pattern matching.
        """
        from sqlalchemy import or_

        conditions = []

        if page_key:
            # Match page_key using case-insensitive LIKE
            conditions.append(PageVisit.page_key.ilike(f"%{page_key}%"))

        if page_alias:
            # Match page_key against the alias pattern
            conditions.append(PageVisit.page_key.ilike(f"%{page_alias}%"))

        if ontology_instance_id:
            # Match page_key against the ontology_instance_id
            conditions.append(PageVisit.page_key.ilike(f"%{ontology_instance_id}%"))

        if not conditions:
            return []

        query = select(PageVisit.page_key).distinct().where(or_(*conditions))
        result = await self.session.execute(query)
        return [row[0] for row in result.all()]
