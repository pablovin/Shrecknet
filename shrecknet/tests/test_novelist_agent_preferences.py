from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.db.migrations import migrate_novelist_agent_preferences
from app.models.agent import Agent
from app.repositories.agent_repository import AgentRepository
from app.schemas.agent import AgentRead
from app.services.agent_service import AgentService
from app.services.novelist_service import NovelistService


@pytest.mark.asyncio
async def test_preferences_resolve_save_and_clear(session_maker) -> None:
    async with session_maker() as session:
        agent = Agent(name="Writer", job="novelist", active=True)
        other = Agent(name="Other Writer", job="novelist", active=True)
        session.add_all([agent, other])
        await session.commit()

        service = NovelistService(session)
        resolved = await service.resolve_and_save_preferences(
            agent_id=agent.id,
            language="fr",
            instructions="Keep names stable",
        )
        await session.commit()
        assert resolved == ("fr", "Keep names stable")

        refreshed = await AgentRepository(session).get_by_id(agent.id)
        assert refreshed is not None
        response = AgentRead.model_validate(
            {
                "id": refreshed.id,
                "name": refreshed.name,
                "job": refreshed.job,
                "active": refreshed.active,
                "created_at": refreshed.created_at,
                "updated_at": refreshed.updated_at,
                "novelist_last_language": refreshed.novelist_last_language,
                "novelist_last_instructions": refreshed.novelist_last_instructions,
            }
        )
        assert response.novelist_last_language == "fr"
        assert response.novelist_last_instructions == "Keep names stable"

        listed = await AgentService(session).list_agents(job="novelist")
        api_agent = next(item for item in listed if item.id == agent.id)
        assert api_agent.novelist_last_language == "fr"
        assert api_agent.novelist_last_instructions == "Keep names stable"

        reused = await service.resolve_and_save_preferences(
            agent_id=agent.id, language=None, instructions=None
        )
        assert reused == resolved

        cleared = await service.resolve_and_save_preferences(
            agent_id=agent.id, language="", instructions=""
        )
        assert cleared == (None, None)
        await session.commit()

        untouched = await AgentRepository(session).get_by_id(other.id)
        assert untouched is not None
        assert untouched.novelist_last_language is None
        assert untouched.novelist_last_instructions is None


def test_novelist_preferences_migration_is_additive_and_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text("""CREATE TABLE agents (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            avatar_url VARCHAR(512),
            description TEXT,
            writing_style TEXT,
            job VARCHAR(50) NOT NULL,
            active BOOLEAN NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )""")
        )
        connection.execute(
            text(
                "INSERT INTO agents (id, name, job, active, created_at, updated_at) "
                "VALUES ('a1', 'Writer', 'novelist', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

        migrate_novelist_agent_preferences(connection)
        migrate_novelist_agent_preferences(connection)

        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(agents)"))}
        assert "novelist_last_language" in columns
        assert "novelist_last_instructions" in columns
        row = connection.execute(
            text("SELECT id, novelist_last_language, novelist_last_instructions FROM agents")
        ).fetchone()
        assert row == ("a1", None, None)
    engine.dispose()


def test_agents_normalization_preserves_existing_novelist_preferences() -> None:
    from app.db.migrations import migrate_agents_table

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text("""CREATE TABLE agents (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                avatar_url VARCHAR(512), description TEXT, writing_style TEXT,
                job VARCHAR(50) NOT NULL, active BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                kind VARCHAR(50), novelist_last_language VARCHAR(100),
                novelist_last_instructions TEXT
            )""")
        )
        connection.execute(
            text("""INSERT INTO agents
                (id, name, job, active, created_at, updated_at,
                 novelist_last_language, novelist_last_instructions)
                VALUES ('a1', 'Writer', 'novelist', 1, CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP, 'fr', 'Keep names stable')""")
        )

        migrate_agents_table(connection)
        row = connection.execute(
            text("SELECT novelist_last_language, novelist_last_instructions FROM agents")
        ).one()
        assert row == ("fr", "Keep names stable")
    engine.dispose()
