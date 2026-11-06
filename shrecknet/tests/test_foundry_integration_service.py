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


@pytest.mark.asyncio
async def test_default_world_is_optional_and_must_exist_in_allowed_scope(session_maker) -> None:
    async with session_maker() as session:
        session.add_all([World(id="a", name="A"), World(id="b", name="B")])
        await session.commit()
        service = FoundryIntegrationService(session)
        for world_id, allowed, message in [("b", ["a"], "allowed Worlds"), ("missing", [], "does not exist")]:
            with pytest.raises(ValueError, match=message):
                await service.create(name="Table", allowed_world_ids=allowed, default_world_id=world_id, created_by_user_id=1)
        integration, key = await service.create(name="Table", allowed_world_ids=["a"], default_world_id="a", created_by_user_id=1)
        assert (await service.validate_key(key)).default_world_id == "a"
        assert integration.default_world_id == "a"


def test_default_world_migration_is_additive_and_idempotent() -> None:
    from sqlalchemy import create_engine, inspect, text
    from app.db.migrations import migrate_foundry_default_world_column

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        migrate_foundry_default_world_column(connection)  # No table yet.
        connection.execute(text("CREATE TABLE foundry_integrations (id VARCHAR(36), name VARCHAR(255))"))
        connection.execute(text("INSERT INTO foundry_integrations VALUES ('old', 'Existing table')"))
        migrate_foundry_default_world_column(connection)
        migrate_foundry_default_world_column(connection)
        assert "default_world_id" in {c["name"] for c in inspect(connection).get_columns("foundry_integrations")}
        assert connection.execute(text("SELECT name, default_world_id FROM foundry_integrations")).one() == ("Existing table", None)
    engine.dispose()
