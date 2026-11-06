from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import secrets
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.foundry_integration import FoundryIntegration
from app.models.world import World
from app.repositories.foundry_integration_repository import FoundryIntegrationRepository
from app.services.worlds import world_service


class FoundryIntegrationService:
    """Owns Foundry setup credentials; it never authorizes world content reads."""

    _KEY_PREFIX = "shreck_foundry_ft_"

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = FoundryIntegrationRepository(session)

    @staticmethod
    def _hash_key(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    async def validate_default_world(self, world_id: str | None, allowed_world_ids: list[str]) -> None:
        if world_id is None:
            return
        if allowed_world_ids and world_id not in allowed_world_ids:
            raise ValueError("Default World must belong to the integration's allowed Worlds")
        if await world_service.get_world_async(self.session, world_id) is None:
            raise ValueError("Default World does not exist")

    async def create(
        self,
        *,
        name: str,
        allowed_world_ids: list[str],
        created_by_user_id: int,
        default_world_id: str | None = None,
    ) -> tuple[FoundryIntegration, str]:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Integration name cannot be empty")
        world_ids = list(dict.fromkeys(item.strip() for item in allowed_world_ids if item.strip()))
        await self.validate_default_world(default_world_id, world_ids)
        key = f"{self._KEY_PREFIX}{secrets.token_urlsafe(32)}"
        integration = FoundryIntegration(
            id=str(uuid4()),
            name=cleaned_name,
            key_prefix=key[:32],
            key_hash=self._hash_key(key),
            allowed_world_ids=world_ids,
            default_world_id=default_world_id,
            created_by_user_id=created_by_user_id,
        )
        await self.repository.create(integration)
        await self.session.commit()
        return integration, key

    async def validate_key(self, key: str) -> FoundryIntegration | None:
        if not key.startswith(self._KEY_PREFIX):
            return None
        integration = await self.repository.get_by_prefix(key[:32])
        if integration is None or not integration.active:
            return None
        if not secrets.compare_digest(integration.key_hash, self._hash_key(key)):
            return None
        integration.last_used_at = datetime.now(timezone.utc)
        await self.session.commit()
        return integration

    async def list_allowed_worlds(self, integration: FoundryIntegration) -> list[World]:
        query = select(World).order_by(World.name.asc())
        allowed = list(integration.allowed_world_ids or [])
        if allowed:
            query = query.where(World.id.in_(allowed))
        result = await self.session.execute(query)
        return list(result.scalars().all())
