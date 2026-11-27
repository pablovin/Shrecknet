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


def _json_nodes(raw: str | None) -> list[dict]:
    try:
        value = json.loads(raw or "[]")
        return [node for node in value if isinstance(node, dict)] if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def _collection_metadata(db: sqlite3.Connection, ontology_id: int) -> dict[str, list[dict]]:
    # Older databases may predate collections. Never infer ownership from a title.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_collections'").fetchone():
        return {}
    result: dict[str, list[dict]] = {}
    for collection in db.execute("""
        SELECT id, name, nodes FROM source_collections
        WHERE ontology_id=? AND source_type='narrative' AND archived=0 ORDER BY name,id
    """, (ontology_id,)):
        nodes = _json_nodes(collection["nodes"])
        by_id = {node.get("id"): node for node in nodes if isinstance(node.get("id"), str)}
        for node in nodes:
            path, seen = [], set()
            cursor = node
            while cursor and cursor.get("id") not in seen:
                seen.add(cursor.get("id"))
                path.insert(0, str(cursor.get("title") or "Untitled"))
                cursor = by_id.get(cursor.get("parent_id"))
            for source_id in node.get("source_ids") or []:
                if isinstance(source_id, str):
                    result.setdefault(source_id, []).append({
                        "collection_id": collection["id"], "collection_name": collection["name"],
                        "entry_id": node.get("id"), "title": str(node.get("title") or ""),
                        "hierarchy": path, "image_key": node.get("image_key"),
                    })
    return result


def _source_candidates(ontology_id: int, database_path: Path | None = None,
                       media_root: Path | None = None) -> list[tuple[ArchitectSource, dict]]:
    with _connection(database_path) as db:
        rows = db.execute("""
            SELECT i.id, i.title, i.source_revision, i.storage_path, i.mime_type,
                   i.source_sha256, a.id AS asset_id, a.section_id,
                   a.storage_path AS asset_path, a.content_sha256 AS asset_hash,
                   (SELECT o.nodes FROM source_organizations o
                    WHERE o.source_id=i.id ORDER BY o.revision DESC LIMIT 1)
                       AS organization_nodes,
                   (SELECT m.instance_id FROM graph_source_imports m
                    WHERE m.ontology_id=i.ontology_id AND m.source_id=i.id
                    ORDER BY m.instance_id LIMIT 1) AS instance_id
            FROM shreckworld_library_items i
            LEFT JOIN source_assets a ON a.source_id=i.id AND a.kind='markdown'
            WHERE i.ontology_id=? AND i.source_type='narrative'
            ORDER BY i.title, i.id, a.section_id
        """, (ontology_id,)).fetchall()
        collection_meta = _collection_metadata(db, ontology_id)
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(str(row["id"]), []).append(row)
    result = []
    for source_id, group in grouped.items():
        first = group[0]
        sections = {node.get("id"): node for node in _json_nodes(first["organization_nodes"])}
        placements = collection_meta.get(source_id, [])
        placement = placements[0] if placements else {}
        title = placement.get("title") or first["title"] or "Untitled narrative"

        def candidate(row, section=None):
            asset = section is not None
            section_id = str(row["section_id"]) if asset else f"source-{source_id}"
            stored = (row["asset_path"] if asset else row["storage_path"]) or ""
            digest = (row["asset_hash"] if asset else row["source_sha256"]) or ""
            doc = ArchitectSource(source_id, section_id, row["asset_id"] if asset else None,
                                  str(title), int(row["source_revision"]), str(digest), stored,
                                  row["instance_id"])
            readiness, reason = "ready", None
            if not asset and row["mime_type"] != "text/markdown":
                readiness, reason = "unavailable", "No Markdown document is attached"
            elif not stored:
                readiness, reason = "unavailable", "Markdown storage path is missing"
            else:
                try:
                    path = _media_file(stored, media_root)
                    if not path.is_file():
                        readiness, reason = "unavailable", "Markdown file is not visible to Architect"
                    elif path.stat().st_size > 4_000_000:
                        readiness, reason = "unavailable", "Markdown exceeds the current Architect size limit"
                    elif path.stat().st_size == 0:
                        readiness, reason = "empty", "Add narrative text and save the document"
                except (ValueError, OSError):
                    readiness, reason = "unavailable", "Markdown storage is inaccessible"
            meta = {
                "document_title": str(first["title"]), "section_title": section.get("title") if asset else None,
                "collection_id": placement.get("collection_id"), "collection_name": placement.get("collection_name"),
                "entry_id": placement.get("entry_id"), "hierarchy": placement.get("hierarchy", []),
                "image_key": placement.get("image_key"), "placements": placements,
                "readiness": readiness, "readiness_reason": reason,
            }
            return doc, meta

        active = []
        for row in group:
            section = sections.get(row["section_id"])
            metadata = section.get("metadata") if section else None
            if row["asset_id"] and isinstance(metadata, dict) and metadata.get("markdown_asset_id") == row["asset_id"]:
                active.append(candidate(row, section))
        # A stale orphan asset must not mask a valid complete Markdown document.
        if not active:
            active.append(candidate(first))
        result.extend(active)
    return result


def list_architect_sources(ontology_id: int, database_path: Path | None = None,
                           media_root: Path | None = None) -> list[ArchitectSource]:
    return [doc for doc, meta in _source_candidates(ontology_id, database_path, media_root)
            if meta["readiness"] == "ready"]


def list_architect_source_catalog(ontology_id: int, database_path: Path | None = None,
                                  media_root: Path | None = None) -> list[dict]:
    return [{**doc.public(), **meta} for doc, meta in _source_candidates(ontology_id, database_path, media_root)]


def load_architect_source(
    ontology_id: int, source_id: str, section_id: str | None = None,
    database_path: Path | None = None, media_root: Path | None = None,
) -> tuple[ArchitectSource, str]:
    sources = [s for s in list_architect_sources(ontology_id, database_path, media_root)
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


def get_narrative_source_info(ontology_id: int, source_id: str) -> dict | None:
    """Scoped document metadata for legacy provenance without paragraph section."""
    with _connection() as db:
        row = db.execute("""
            SELECT id, title, description, source_revision
            FROM shreckworld_library_items
            WHERE ontology_id=? AND id=? AND source_type='narrative'
        """, (ontology_id, source_id)).fetchone()
        return {
            "source_id": str(row["id"]),
            "title": str(row["title"]),
            "description": str(row["description"] or ""),
            "revision": int(row["source_revision"]),
        } if row is not None else None


def source_belongs_to_ontology(ontology_id: int, source_id: str) -> bool:
    """Check a legacy graph-linked Source without assuming it has a Markdown file.

    Legacy records can validly identify a Source but not its exact section.
    This ownership check never treats that partial reference as a verified
    paragraph-level citation.
    """
    with _connection() as db:
        row = db.execute("""
            SELECT 1 FROM shreckworld_library_items
            WHERE ontology_id=? AND id=? AND source_type='narrative' LIMIT 1
        """, (ontology_id, source_id)).fetchone()
        return row is not None


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
