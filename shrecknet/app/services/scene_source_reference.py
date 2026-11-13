"""Read-only lookup of ShreckWorld Source IDs for legacy graph entities.

The existing ShreckWorld database is mounted under /backup-sources/shreckworld/data
by shrecknet_api and shrecknet_worker. This is a look-up, never a SQL write.
"""
from __future__ import annotations

from pathlib import Path
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)


def resolve_source_reference(
    ontology_id: int, instance_id: str, entity_instance_id: str,
    database_path: Path | None = None,
) -> dict[str, str | None]:
    """Return the exact migrated Source+section identity, or no references.

    Source paragraph offsets may originate from a historical legacy entity text.
    Therefore, no Source revision is asserted without verified text alignment.
    """
    path = database_path or Path(os.getenv(
        "SHRECKNET_SHRECKWORLD_DATABASE_PATH",
        "/backup-sources/shreckworld/data/shreckworld.db",
    ))
    if not path.is_file():
        return {}
    try:
        with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=3) as conn:
            row = conn.execute("""
                SELECT m.source_id, m.section_id, a.id
                FROM graph_source_imports AS m
                LEFT JOIN source_assets AS a
                  ON a.source_id = m.source_id AND a.section_id = m.section_id
                 AND a.kind = 'markdown'
                WHERE m.ontology_id = ? AND m.instance_id = ? AND m.entity_instance_id = ?
                LIMIT 1
            """, (ontology_id, instance_id, entity_instance_id)).fetchone()
    except sqlite3.Error as exc:
        logger.warning("ShreckWorld source mapping lookup unavailable (%s)", type(exc).__name__)
        return {}
    if row is None:
        return {}
    return {"source_id": row[0], "section_id": row[1], "markdown_asset_id": row[2]}
