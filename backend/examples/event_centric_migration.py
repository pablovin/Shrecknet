"""Run offline event-centric Neo4j migration."""

from __future__ import annotations

import asyncio
import json

from app.core.config_store import get_settings
from app.db.event_centric_migration import migrate_event_centric_schema
from app.graph.neo4j import close_driver, get_driver


async def main() -> None:
    settings = get_settings()
    driver = get_driver()
    try:
        async with driver.session(database=settings.neo4j_database) as session:
            result = await migrate_event_centric_schema(session)
            print(json.dumps(result, indent=2, default=str))
    finally:
        await close_driver()


if __name__ == "__main__":
    asyncio.run(main())
