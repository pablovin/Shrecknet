from __future__ import annotations

from typing import Sequence

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.favorite_ontology_instance import favorite_ontology_instances


class FavoriteOntologyInstanceRepository:
    """Repository for managing user favorite ontology instances."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_favorite(
        self, user_id: int, instance_id: str, ontology_id: int
    ) -> dict:
        """Add an ontology instance to user's favorites."""
        # Check if already exists
        existing = await self.session.execute(
            select(favorite_ontology_instances).where(
                favorite_ontology_instances.c.user_id == user_id,
                favorite_ontology_instances.c.instance_id == instance_id,
            )
        )
        if existing.first():
            # Already favorited, return existing
            result = await self.session.execute(
                select(favorite_ontology_instances).where(
                    favorite_ontology_instances.c.user_id == user_id,
                    favorite_ontology_instances.c.instance_id == instance_id,
                )
            )
            row = result.first()
            return {
                "id": row.id,
                "user_id": row.user_id,
                "instance_id": row.instance_id,
                "ontology_id": row.ontology_id,
                "created_at": row.created_at,
            }

        # Insert new favorite
        result = await self.session.execute(
            favorite_ontology_instances.insert().values(
                user_id=user_id,
                instance_id=instance_id,
                ontology_id=ontology_id,
            )
        )
        await self.session.flush()

        # Fetch the created record
        new_id = result.inserted_primary_key[0]
        created = await self.session.execute(
            select(favorite_ontology_instances).where(
                favorite_ontology_instances.c.id == new_id
            )
        )
        row = created.first()
        return {
            "id": row.id,
            "user_id": row.user_id,
            "instance_id": row.instance_id,
            "ontology_id": row.ontology_id,
            "created_at": row.created_at,
        }

    async def remove_favorite(self, user_id: int, instance_id: str) -> bool:
        """Remove an ontology instance from user's favorites."""
        result = await self.session.execute(
            delete(favorite_ontology_instances).where(
                favorite_ontology_instances.c.user_id == user_id,
                favorite_ontology_instances.c.instance_id == instance_id,
            )
        )
        await self.session.flush()
        return result.rowcount > 0

    async def list_favorites(
        self, user_id: int, skip: int = 0, limit: int = 50
    ) -> Sequence[dict]:
        """List all favorite ontology instances for a user."""
        result = await self.session.execute(
            select(favorite_ontology_instances)
            .where(favorite_ontology_instances.c.user_id == user_id)
            .order_by(favorite_ontology_instances.c.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        rows = result.fetchall()
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
        """Check if an ontology instance is favorited by a user."""
        result = await self.session.execute(
            select(favorite_ontology_instances).where(
                favorite_ontology_instances.c.user_id == user_id,
                favorite_ontology_instances.c.instance_id == instance_id,
            )
        )
        return result.first() is not None

    async def get_users_who_favorited(self, instance_id: str) -> list[int]:
        """Get all user IDs who have favorited a specific instance."""
        result = await self.session.execute(
            select(favorite_ontology_instances.c.user_id).where(
                favorite_ontology_instances.c.instance_id == instance_id
            )
        )
        rows = result.fetchall()
        return [row.user_id for row in rows]
