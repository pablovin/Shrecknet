"""Backfill world identity without rewriting cross-service storage IDs."""

import logging

from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)

_COLUMNS = {
    "description": "TEXT",
    "image_url": "VARCHAR(512)",
    "rpg_system": "VARCHAR(255)",
    "rpg_edition": "VARCHAR(100)",
    "rpg_subversion": "VARCHAR(100)",
}


def migrate_world_identity(connection) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(73921462)"))
    columns = {column["name"] for column in inspect(connection).get_columns("worlds")}
    for name, definition in _COLUMNS.items():
        if name in columns:
            continue
        connection.execute(text(f"ALTER TABLE worlds ADD COLUMN {name} {definition}"))
        if name in {"description", "image_url", "rpg_system"}:
            connection.execute(text(f"""
                UPDATE worlds SET {name} = (
                    SELECT {name} FROM ontologies WHERE ontologies.world_id = worlds.id
                )
                WHERE (SELECT COUNT(*) FROM ontologies WHERE ontologies.world_id = worlds.id) = 1
            """))

    orphans = connection.execute(text("""
        SELECT o.id, o.world_id, o.name, o.description, o.image_url, o.rpg_system
        FROM ontologies o LEFT JOIN worlds w ON w.id = o.world_id
        WHERE w.id IS NULL ORDER BY o.id
    """)).mappings().all()
    for row in orphans:
        # Reuse a historical ID only if it identifies this ontology alone.
        world_id = row["world_id"]
        if world_id is not None:
            count = connection.execute(text(
                "SELECT COUNT(*) FROM ontologies WHERE world_id = :world_id"
            ), {"world_id": world_id}).scalar_one()
            if count != 1:
                logger.warning("Unresolved world %s includes ontology %s", world_id, row["id"])
                continue
        else:
            world_id = f"world-onto-{row['id']}"
            suffix = 0
            while connection.execute(text(
                "SELECT 1 FROM worlds WHERE id = :id UNION ALL SELECT 1 FROM ontologies WHERE world_id = :id"
            ), {"id": world_id}).first() is not None:
                suffix += 1
                world_id = f"world-onto-{row['id']}-{suffix}"
        connection.execute(text("""
            INSERT INTO worlds (id, name, description, image_url, rpg_system)
            VALUES (:world_id, :name, :description, :image_url, :rpg_system)
        """), {**row, "world_id": world_id})
        connection.execute(text("UPDATE ontologies SET world_id = :world_id WHERE id = :id"),
                           {"world_id": world_id, "id": row["id"]})

    ambiguous = connection.execute(text("""
        SELECT world_id FROM ontologies WHERE world_id IS NOT NULL
        GROUP BY world_id HAVING COUNT(*) > 1 ORDER BY world_id
    """)).scalars().all()
    if ambiguous:
        logger.warning("Worlds with multiple ontology scopes require reconciliation: %s", ambiguous)
