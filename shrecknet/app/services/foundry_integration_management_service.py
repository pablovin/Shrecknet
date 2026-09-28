from __future__ import annotations

from datetime import datetime, timezone
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.foundry_integration import FoundryIntegration
from app.repositories.foundry_integration_repository import FoundryIntegrationRepository
from app.services.foundry_integration_service import FoundryIntegrationService


class FoundryIntegrationManagementService:
    """Administrative lifecycle operations used by Shrecknet's config manager."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = FoundryIntegrationRepository(session)

    async def get(self, integration_id: str) -> FoundryIntegration | None:
        return next(
            (item for item in await self.repository.list() if item.id == integration_id),
            None,
        )

    async def update(
        self,
        integration: FoundryIntegration,
        *,
        name: str | None,
        allowed_world_ids: list[str] | None,
    ) -> FoundryIntegration:
        if name is not None:
            integration.name = name.strip()
        if allowed_world_ids is not None:
            integration.allowed_world_ids = list(
                dict.fromkeys(value.strip() for value in allowed_world_ids if value.strip())
            )
        await self.session.commit()
        await self.session.refresh(integration)
        return integration

    async def rotate(self, integration: FoundryIntegration) -> tuple[FoundryIntegration, str]:
        key = f"{FoundryIntegrationService._KEY_PREFIX}{secrets.token_urlsafe(32)}"
        integration.key_prefix = key[:32]
        integration.key_hash = FoundryIntegrationService._hash_key(key)
        integration.active = True
        integration.revoked_at = None
        integration.last_used_at = None
        await self.session.commit()
        await self.session.refresh(integration)
        return integration, key

    async def revoke(self, integration: FoundryIntegration) -> None:
        integration.active = False
        integration.revoked_at = datetime.now(timezone.utc)
        await self.session.commit()
