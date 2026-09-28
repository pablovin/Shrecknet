from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.foundry_integration import FoundryIntegration


class FoundryIntegrationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, integration: FoundryIntegration) -> FoundryIntegration:
        self.session.add(integration)
        await self.session.flush()
        await self.session.refresh(integration)
        return integration

    async def get_by_prefix(self, key_prefix: str) -> FoundryIntegration | None:
        result = await self.session.execute(
            select(FoundryIntegration).where(FoundryIntegration.key_prefix == key_prefix)
        )
        return result.scalar_one_or_none()

    async def list(self) -> list[FoundryIntegration]:
        result = await self.session.execute(
            select(FoundryIntegration).order_by(FoundryIntegration.created_at.desc())
        )
        return list(result.scalars().all())
