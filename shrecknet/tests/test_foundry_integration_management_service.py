from __future__ import annotations

import pytest

from app.services.foundry_integration_management_service import FoundryIntegrationManagementService
from app.services.foundry_integration_service import FoundryIntegrationService


@pytest.mark.asyncio
async def test_config_manager_can_update_rotate_and_revoke_foundry_key(session_maker) -> None:
    async with session_maker() as session:
        keys = FoundryIntegrationService(session)
        integration, first_key = await keys.create(
            name="Initial integration",
            allowed_world_ids=["world-a"],
            created_by_user_id=1,
        )
        manager = FoundryIntegrationManagementService(session)

        updated = await manager.update(
            integration,
            name="Renamed integration",
            allowed_world_ids=["world-b", "world-b"],
        )
        assert updated.name == "Renamed integration"
        assert updated.allowed_world_ids == ["world-b"]

        rotated, second_key = await manager.rotate(updated)
        assert second_key != first_key
        assert await keys.validate_key(first_key) is None
        assert (await keys.validate_key(second_key)).id == rotated.id

        await manager.revoke(rotated)
        assert rotated.active is False
        assert rotated.revoked_at is not None
        assert await keys.validate_key(second_key) is None


@pytest.mark.asyncio
async def test_default_world_update_preserves_omitted_value_and_can_clear_it(session_maker) -> None:
    from app.models.world import World
    async with session_maker() as session:
        session.add_all([World(id="a", name="A"), World(id="b", name="B")])
        await session.commit()
        integration, _ = await FoundryIntegrationService(session).create(
            name="Table", allowed_world_ids=["a"], default_world_id="a", created_by_user_id=1,
        )
        manager = FoundryIntegrationManagementService(session)
        await manager.update(integration, name="Renamed", allowed_world_ids=None)
        assert integration.default_world_id == "a"
        with pytest.raises(ValueError, match="allowed Worlds"):
            await manager.update(integration, name="Do not save", allowed_world_ids=["b"])
        assert integration.name == "Renamed"
        assert integration.allowed_world_ids == ["a"]
        await manager.update(integration, name=None, allowed_world_ids=["b"], default_world_id="b", update_default_world=True)
        assert integration.default_world_id == "b"
        await manager.update(integration, name=None, allowed_world_ids=None, default_world_id=None, update_default_world=True)
        assert integration.default_world_id is None
