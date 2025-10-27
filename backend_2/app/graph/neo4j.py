from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession
from neo4j.exceptions import Neo4jError

from app.core.config import get_settings

_driver: AsyncDriver | None = None


def get_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        settings = get_settings()
        _driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_lifetime=3600,
        )
    return _driver


async def close_driver() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


async def get_neo4j_session() -> AsyncGenerator[AsyncSession, None]:
    driver = get_driver()
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        yield session


async def get_optional_neo4j_session() -> AsyncGenerator[AsyncSession | None, None]:
    try:
        driver = get_driver()
        settings = get_settings()
    except Neo4jError:
        yield None
        return
    try:
        async with driver.session(database=settings.neo4j_database) as session:
            yield session
    except Neo4jError:
        yield None
