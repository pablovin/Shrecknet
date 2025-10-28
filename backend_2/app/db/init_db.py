from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.base import Base


def _ensure_entity_display_column(sync_conn) -> None:
    inspector = inspect(sync_conn)
    columns = {column["name"] for column in inspector.get_columns("ontology_entities")}
    if "display_on_world" not in columns:
        sync_conn.execute(
            text(
                "ALTER TABLE ontology_entities "
                "ADD COLUMN display_on_world BOOLEAN NOT NULL DEFAULT 1"
            )
        )


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_entity_display_column)
