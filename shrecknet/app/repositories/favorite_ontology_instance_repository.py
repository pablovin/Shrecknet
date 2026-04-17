from __future__ import annotations

from typing import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ontology_instance import FavoriteOntologyInstance


class FavoriteOntologyInstanceRepository:
    """Repository for managing user favorite ontology instances."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_favorite(
        self, user_id: int, instance_id: str, ontology_id: int
    ) -> dict:
        existing = await self.session.scalar(
            select(FavoriteOntologyInstance).where(
                FavoriteOntologyInstance.user_id == user_id,
                FavoriteOntologyInstance.instance_id == instance_id,
            )
        )
        if existing is not None:
            return {
                "id": existing.id,
                "user_id": existing.user_id,
                "instance_id": existing.instance_id,
                "ontology_id": existing.ontology_id,
                "created_at": existing.created_at,
            }

        favorite = FavoriteOntologyInstance(
            user_id=user_id,
            instance_id=instance_id,
            ontology_id=ontology_id,
        )
        self.session.add(favorite)
        await self.session.flush()
        return {
            "id": favorite.id,
            "user_id": favorite.user_id,
            "instance_id": favorite.instance_id,
            "ontology_id": favorite.ontology_id,
            "created_at": favorite.created_at,
        }

    async def remove_favorite(self, user_id: int, instance_id: str) -> bool:
        result = await self.session.execute(
            delete(FavoriteOntologyInstance).where(
                FavoriteOntologyInstance.user_id == user_id,
                FavoriteOntologyInstance.instance_id == instance_id,
            )
        )
        await self.session.flush()
        return bool(result.rowcount)

    async def list_favorites(
        self, user_id: int, skip: int = 0, limit: int = 50
    ) -> Sequence[dict]:
        rows = (
            await self.session.execute(
                select(FavoriteOntologyInstance)
                .where(FavoriteOntologyInstance.user_id == user_id)
                .order_by(FavoriteOntologyInstance.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
        ).scalars().all()
        return [
            {
                "id": row.id,
                "user_id": row.user_id,
                "instance_id": row.instance_id,
                "ontology_id": row.ontology_id,
                "created_at": row.created_at,
            }
            for row in rows
        ]

    async def is_favorite(self, user_id: int, instance_id: str) -> bool:
        row = await self.session.scalar(
            select(FavoriteOntologyInstance.id).where(
                FavoriteOntologyInstance.user_id == user_id,
                FavoriteOntologyInstance.instance_id == instance_id,
            )
        )
        return row is not None

    async def get_users_who_favorited(self, instance_id: str) -> list[int]:
        rows = await self.session.scalars(
            select(FavoriteOntologyInstance.user_id).where(
                FavoriteOntologyInstance.instance_id == instance_id
            )
        )
        return list(rows)
