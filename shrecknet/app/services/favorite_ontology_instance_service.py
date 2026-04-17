from __future__ import annotations

from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.favorite_ontology_instance_repository import (
    FavoriteOntologyInstanceRepository,
)


class FavoriteOntologyInstanceService:
    """Service for managing favorite ontology instances."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = FavoriteOntologyInstanceRepository(session)

    async def add_favorite(
        self, user_id: int, instance_id: str, ontology_id: int
    ) -> dict:
        favorite = await self.repository.add_favorite(user_id, instance_id, ontology_id)
        await self.session.commit()
        return favorite

    async def remove_favorite(self, user_id: int, instance_id: str) -> bool:
        removed = await self.repository.remove_favorite(user_id, instance_id)
        await self.session.commit()
        return removed

    async def list_favorites(
        self, user_id: int, skip: int = 0, limit: int = 50
    ) -> Sequence[dict]:
        return await self.repository.list_favorites(user_id, skip, limit)

    async def is_favorite(self, user_id: int, instance_id: str) -> bool:
        return await self.repository.is_favorite(user_id, instance_id)

    async def get_users_who_favorited(self, instance_id: str) -> list[int]:
        return await self.repository.get_users_who_favorited(instance_id)
