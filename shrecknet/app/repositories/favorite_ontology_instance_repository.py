from __future__ import annotations

from typing import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class FavoriteOntologyInstanceRepository:
    """Repository for managing user favorite ontology instances."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._table_ready = False

    async def _ensure_table(self) -> None:
        if self._table_ready:
            return
        await self.session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS favorite_ontology_instances_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    instance_id VARCHAR(64) NOT NULL,
                    ontology_id INTEGER NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, instance_id)
                )
                """
            )
        )
        old_table_exists = await self.session.execute(
            text(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'favorite_ontology_instances'
                LIMIT 1
                """
            )
        )
        if old_table_exists.first() is not None:
            await self.session.execute(
                text(
                    """
                    INSERT OR IGNORE INTO favorite_ontology_instances_v2
                        (id, user_id, instance_id, ontology_id, created_at)
                    SELECT id, user_id, instance_id, ontology_id, created_at
                    FROM favorite_ontology_instances
                    """
                )
            )
        self._table_ready = True

    async def add_favorite(
        self, user_id: int, instance_id: str, ontology_id: int
    ) -> dict:
        await self._ensure_table()
        existing = (
            await self.session.execute(
                text(
                    """
                    SELECT id, user_id, instance_id, ontology_id, created_at
                    FROM favorite_ontology_instances_v2
                    WHERE user_id = :user_id AND instance_id = :instance_id
                    LIMIT 1
                    """
                ),
                {"user_id": user_id, "instance_id": instance_id},
            )
        )
        existing_row = existing.mappings().first()
        if existing_row is not None:
            return {
                "id": existing_row["id"],
                "user_id": existing_row["user_id"],
                "instance_id": existing_row["instance_id"],
                "ontology_id": existing_row["ontology_id"],
                "created_at": existing_row["created_at"],
            }

        await self.session.execute(
            text(
                """
                INSERT INTO favorite_ontology_instances_v2 (user_id, instance_id, ontology_id)
                VALUES (:user_id, :instance_id, :ontology_id)
                """
            ),
            {"user_id": user_id, "instance_id": instance_id, "ontology_id": ontology_id},
        )
        created = await self.session.execute(
            text(
                """
                SELECT id, user_id, instance_id, ontology_id, created_at
                FROM favorite_ontology_instances_v2
                WHERE user_id = :user_id AND instance_id = :instance_id
                LIMIT 1
                """
            ),
            {"user_id": user_id, "instance_id": instance_id},
        )
        created_row = created.mappings().first()
        if created_row is None:
            raise RuntimeError("Favorite insert failed")
        return {
            "id": created_row["id"],
            "user_id": created_row["user_id"],
            "instance_id": created_row["instance_id"],
            "ontology_id": created_row["ontology_id"],
            "created_at": created_row["created_at"],
        }

    async def remove_favorite(self, user_id: int, instance_id: str) -> bool:
        await self._ensure_table()
        result = await self.session.execute(
            text(
                """
                DELETE FROM favorite_ontology_instances_v2
                WHERE user_id = :user_id AND instance_id = :instance_id
                """
            ),
            {"user_id": user_id, "instance_id": instance_id},
        )
        await self.session.flush()
        return bool(result.rowcount)

    async def list_favorites(
        self, user_id: int, skip: int = 0, limit: int = 50
    ) -> Sequence[dict]:
        await self._ensure_table()
        result = await self.session.execute(
            text(
                """
                SELECT id, user_id, instance_id, ontology_id, created_at
                FROM favorite_ontology_instances_v2
                WHERE user_id = :user_id
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :skip
                """
            ),
            {"user_id": user_id, "skip": skip, "limit": limit},
        )
        rows = result.mappings().all()
        return [
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "instance_id": row["instance_id"],
                "ontology_id": row["ontology_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def is_favorite(self, user_id: int, instance_id: str) -> bool:
        await self._ensure_table()
        row = await self.session.execute(
            text(
                """
                SELECT id
                FROM favorite_ontology_instances_v2
                WHERE user_id = :user_id AND instance_id = :instance_id
                LIMIT 1
                """
            ),
            {"user_id": user_id, "instance_id": instance_id},
        )
        return row.first() is not None

    async def get_users_who_favorited(self, instance_id: str) -> list[int]:
        await self._ensure_table()
        rows = await self.session.execute(
            text(
                """
                SELECT user_id
                FROM favorite_ontology_instances_v2
                WHERE instance_id = :instance_id
                """
            ),
            {"instance_id": instance_id},
        )
        return [int(row[0]) for row in rows.all()]
