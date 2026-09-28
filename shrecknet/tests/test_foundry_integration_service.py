from __future__ import annotations

import pytest

from app.models.world import World
from app.services.foundry_integration_service import FoundryIntegrationService


@pytest.mark.asyncio
async def test_foundry_key_is_validated_and_world_scope_is_applied(session_maker) -> None:
    async with session_maker() as session:
        session.add_all([World(id="edinburgh", name="Edinburgh"), World(id="london", name="London")])
        await session.commit()

        service = FoundryIntegrationService(session)
        integration, key = await service.create(
            name="Foundry Edinburgh",
            allowed_world_ids=["edinburgh", "edinburgh"],
            created_by_user_id=1,
        )

        assert key.startswith("shreck_foundry_ft_")
        assert integration.key_hash != key
        assert integration.allowed_world_ids == ["edinburgh"]
        assert await service.validate_key("shreck_foundry_ft_invalid") is None

        validated = await service.validate_key(key)
        assert validated is not None
        assert validated.id == integration.id
        assert validated.last_used_at is not None

        worlds = await service.list_allowed_worlds(validated)
        assert [(world.id, world.name) for world in worlds] == [("edinburgh", "Edinburgh")]


@pytest.mark.asyncio
async def test_empty_foundry_world_scope_permits_all_worlds(session_maker) -> None:
    async with session_maker() as session:
        session.add_all([World(id="a", name="A"), World(id="b", name="B")])
        await session.commit()
        service = FoundryIntegrationService(session)
        integration, _ = await service.create(name="All Worlds", allowed_world_ids=[], created_by_user_id=1)

        assert [world.id for world in await service.list_allowed_worlds(integration)] == ["a", "b"]
