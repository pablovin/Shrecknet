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
