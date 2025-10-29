from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession
from neo4j.exceptions import Neo4jError

from app.core.config import get_settings

_driver: AsyncDriver | None = None
_driver_loop: asyncio.AbstractEventLoop | None = None


def get_driver() -> AsyncDriver:
    """
    Get the Neo4j async driver, ensuring it's bound to the current event loop.
    
    This function is event-loop-aware: if called from different event loops,
    it will close the old driver and create a new one for the current loop.
    This prevents "attached to a different loop" errors in Celery tasks.
    
    Returns:
        AsyncDriver instance bound to the current event loop
    """
    global _driver, _driver_loop
    
    # Try to get the current event loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop is running, we'll create the driver without loop tracking
        current_loop = None
    
    # Check if we need to recreate the driver
    # This happens when:
    # 1. Driver doesn't exist
    # 2. We're in a different event loop than when driver was created
    if _driver is None or (_driver_loop is not None and current_loop is not _driver_loop):
        # Reset driver if switching loops
        if _driver is not None and current_loop is not _driver_loop:
            # We can't await the close here since this is a sync function
            # The driver will be garbage collected
            _driver = None
            _driver_loop = None
        
        settings = get_settings()
        _driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_lifetime=3600,
        )
        _driver_loop = current_loop
    
    return _driver


async def close_driver() -> None:
    """Close the Neo4j driver and reset loop tracking."""
    global _driver, _driver_loop
    if _driver is not None:
        await _driver.close()
        _driver = None
        _driver_loop = None


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
