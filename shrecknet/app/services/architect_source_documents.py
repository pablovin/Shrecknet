"""Read-only, scoped ShreckWorld narrative documents for Architect.

The Shrecknet API and worker already mount the ShreckWorld SQLite database and
media directory for backup. Never execute arbitrary storage paths from SQL:
only files beneath the read-only ShreckWorld media mount may be read.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class ArchitectSource:
    source_id: str
    section_id: str
    markdown_asset_id: str | None
    title: str
    source_revision: int
    content_sha256: str
    storage_path: str
    legacy_instance_id: str | None = None

    def public(self) -> dict:
        return {k: getattr(self, k) for k in (
            "source_id", "section_id", "markdown_asset_id", "title",
            "source_revision", "content_sha256", "legacy_instance_id",
        )}


def _connection(database_path: Path | None = None) -> sqlite3.Connection:
    db_path = database_path or Path(os.getenv(
        "SHRECKNET_SHRECKWORLD_DATABASE_PATH",
        "/backup-sources/shreckworld/data/shreckworld.db",
    ))
    if not db_path.is_file():
        raise FileNotFoundError("ShreckWorld database mount is unavailable")
    db = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro",
                         uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def _media_file(stored: str, media_root: Path | None = None) -> Path:
    root = (media_root or Path(os.getenv(
        "SHRECKNET_SHRECKWORLD_MEDIA_PATH",
        "/backup-sources/shreckworld/media",
    ))).resolve()
    # ShreckWorld persists container paths under /app/media; translate the
    # prefix into the shared mount, never trust arbitrary absolute SQL paths.
    origin = Path(stored)
    relative = origin.relative_to(Path("/app/media"))
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Source asset escaped the protected media directory")
    return candidate


def list_architect_sources(ontology_id: int, database_path: Path | None = None) -> list[ArchitectSource]:
    with _connection(database_path) as db:
        rows = db.execute("""
            SELECT i.id, i.title, i.source_revision, i.storage_path, i.mime_type,
                   i.source_sha256, a.id AS asset_id, a.section_id,
                   a.storage_path AS asset_path, a.content_sha256 AS asset_hash,
                   (SELECT m.instance_id FROM graph_source_imports m
                    WHERE m.ontology_id=i.ontology_id AND m.source_id=i.id
                    ORDER BY m.instance_id LIMIT 1) AS instance_id
            FROM shreckworld_library_items i
            LEFT JOIN source_assets a ON a.source_id=i.id AND a.kind='markdown'
            WHERE i.ontology_id=? AND i.source_type='narrative'
              AND (a.id IS NOT NULL OR (i.mime_type='text/markdown'
                   AND COALESCE(i.storage_path, '') <> ''))
            ORDER BY i.title, a.section_id
        """, (ontology_id,)).fetchall()
        result = []
        for row in rows:
            stored = row["asset_path"] or row["storage_path"] or ""
            if not stored:
                continue
            try:
                path = _media_file(stored)
                if not path.is_file() or path.stat().st_size > 4_000_000:
                    continue
            except (ValueError, OSError):
                continue
            result.append(ArchitectSource(
                source_id=str(row["id"]),
                section_id=str(row["section_id"] or f"source-{row['id']}"),
                markdown_asset_id=row["asset_id"],
                title=str(row["title"]),
                source_revision=int(row["source_revision"]),
                content_sha256=str(row["asset_hash"] or row["source_sha256"]),
                storage_path=stored,
                legacy_instance_id=row["instance_id"],
            ))
        return result


def load_architect_source(
    ontology_id: int, source_id: str, section_id: str | None = None,
    database_path: Path | None = None, media_root: Path | None = None,
) -> tuple[ArchitectSource, str]:
    sources = [s for s in list_architect_sources(ontology_id, database_path)
               if s.source_id == source_id and (section_id is None or s.section_id == section_id)]
    if len(sources) != 1:
        raise ValueError("Select exactly one existing narrative Markdown section")
    selected = sources[0]
    path = _media_file(selected.storage_path, media_root)
    content = path.read_bytes()
    if sha256(content).hexdigest() != selected.content_sha256:
        raise ValueError("Narrative Markdown changed since its saved Source revision")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Narrative Source is not UTF-8 Markdown") from exc
    if not text.strip():
        raise ValueError("Selected narrative Source has no text")
    return selected, text


def imported_narrative_entity_ids(ontology_id: int) -> set[str]:
    """Narrative graph documents migrated to ShreckWorld (never graph authority)."""
    try:
        with _connection() as db:
            return {str(row[0]) for row in db.execute("""
                SELECT m.entity_instance_id FROM graph_source_imports m
                JOIN shreckworld_library_items i ON i.id = m.source_id
                WHERE m.ontology_id=? AND i.source_type='narrative'
            """, (ontology_id,))}
    except (FileNotFoundError, OSError, sqlite3.Error):
        # Do not disable ordinary ontology embeddings if ShreckWorld is offline.
        return set()
