from __future__ import annotations

import pytest

from app.repositories.foundry_integration_repository import FoundryIntegrationRepository


@pytest.mark.asyncio
async def test_list_foundry_integrations_returns_an_empty_list(session_maker) -> None:
    async with session_maker() as session:
        repository = FoundryIntegrationRepository(session)

        integrations = await repository.list()

    assert integrations == []
