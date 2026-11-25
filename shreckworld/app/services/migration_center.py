"""Conservative, inspectable migration orchestration.

Only updates NULL ontology_id on legacy source items, retaining historical
ShreckWorld ownership, document identifiers, storage paths and all other fields.
Unresolved world/table/graph/chronicle migrations are reported but never guessed.
"""
from __future__ import annotations

from hashlib import sha256
import json

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.integrations.ontologies import shrecknet_read
from app.models import ShreckWorld, ShreckWorldLibraryItem
from app.models.source_collection import SourceCollection
from app.models.shrecktables import LegacyTableMap, SessionNarrativeSource, Table, TableSession
from app.models.migration_center import MigrationCenterAudit
from app.services.world_rules import read_world_rules


def _world_directory(token: str) -> list[dict]:
    raw = shrecknet_read("/worlds", token)
    if not isinstance(raw, list):
        raise HTTPException(503, "Invalid world directory")
    result = []
    seen_world_ids: set[str] = set()
    for value in raw:
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise HTTPException(503, "Invalid world directory")
        ident = value["id"]
        ids = value.get("ontology_ids")
        if ident in seen_world_ids or not isinstance(ids, list) or any(type(x) is not int or x <= 0 for x in ids):
            raise HTTPException(503, "Invalid world directory")
        seen_world_ids.add(ident)
        # Never infer a canonical ontology from the first entry of a multi-ontology world.
        active = value.get("active_ontology_id")
        if active is None and len(ids) == 1:
            active = ids[0]
        if active is not None and (type(active) is not int or active not in ids):
            raise HTTPException(503, "Invalid world directory")
        result.append({"id": ident, "name": str(value.get("name") or ident),
                       "ontology_ids": ids, "active_ontology_id": active})
    return result


def _sources(db: Session, collection_id: str) -> list[ShreckWorldLibraryItem]:
    return list(db.scalars(select(ShreckWorldLibraryItem).where(
        ShreckWorldLibraryItem.shreckworld_id == collection_id
    ).order_by(ShreckWorldLibraryItem.id)))


def _target(worlds: list[dict], collection: ShreckWorld, target: int) -> dict:
    matches = [w for w in worlds if w["active_ontology_id"] == target]
    if len(matches) != 1:
        raise HTTPException(409, "Target ontology must be the unique active ontology of one Shreckworld")
    chosen = matches[0]
    if collection.shrecknet_world_id and collection.shrecknet_world_id != chosen["id"]:
        raise HTTPException(409, "Legacy collection is linked to a different world; reconcile the world identity first")
    return chosen


def _fingerprint(collection: ShreckWorld, target: int, world: dict, items: list[ShreckWorldLibraryItem]) -> str:
    evidence = {
        "collection": collection.id,
        "legacy_world_id": collection.shrecknet_world_id,
        "world": world["id"],
        "world_ontology_ids": world["ontology_ids"],
        "target": target,
        "items": [(item.id, item.ontology_id, item.source_revision, item.source_sha256) for item in items],
    }
    return sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _preview(db: Session, token: str, collection_id: str, target: int) -> dict:
    collection = db.get(ShreckWorld, collection_id)
    if collection is None:
        raise HTTPException(404, "Legacy collection not found")
    worlds = _world_directory(token)
    world = _target(worlds, collection, target)
    items = _sources(db, collection_id)
    conflicts = [
        {"id": item.id, "title": item.title, "existing_ontology_id": item.ontology_id}
        for item in items if item.ontology_id not in (None, target)
    ]
    pending = [item for item in items if item.ontology_id is None]
    return {
        "collection_id": collection.id,
        "collection_name": collection.name,
        "target_world_id": world["id"],
        "target_world_name": world["name"],
        "target_ontology_id": target,
        "source_count": len(items),
        "will_update": len(pending),
        "already_mapped": len(items) - len(pending) - len(conflicts),
        "conflicts": conflicts,
        "blocked": bool(conflicts),
        "preview_fingerprint": _fingerprint(collection, target, world, items),
        "samples": [{"id": item.id, "title": item.title,
                     "source_type": item.source_type,
                     "current_ontology_id": item.ontology_id,
                     "action": "add_world_ontology_reference" if item.ontology_id is None else
                     ("unchanged" if item.ontology_id == target else "blocked_conflict")}
                    for item in items[:100]],
        "preserves": ["legacy collection reference", "source IDs", "files", "BookMaps",
                      "document generations", "embeddings", "Neo4j", "Qdrant", "source revisions"],
    }


def preview(db: Session, token: str, collection_id: str, target: int) -> dict:
    return _preview(db, token, collection_id, target)


def overview(db: Session, token: str) -> dict:
    worlds = _world_directory(token)
    collections = list(db.scalars(select(ShreckWorld).order_by(ShreckWorld.name, ShreckWorld.id)))
    source_counts = db.execute(select(
        ShreckWorldLibraryItem.shreckworld_id,
        ShreckWorldLibraryItem.ontology_id,
        func.count()
    ).group_by(ShreckWorldLibraryItem.shreckworld_id, ShreckWorldLibraryItem.ontology_id)).all()
    counts: dict[str | None, dict[int | None, int]] = {}
    for legacy_id, ontology_id, count in source_counts:
        counts.setdefault(legacy_id, {})[ontology_id] = count
    collection_rows = []
    for coll in collections:
        match = next((w for w in worlds if w["id"] == coll.shrecknet_world_id), None)
        ids = match["ontology_ids"] if match else []
        suggested = match["active_ontology_id"] if match else None
        if len(ids) != 1:
            suggested = None
        existing = counts.get(coll.id, {})
        pending = existing.get(None, 0)
        conflicting = sum(v for k, v in existing.items() if k is not None and suggested is not None and k != suggested)
        status = ("needs_mapping" if suggested is None else
                  "conflict" if conflicting else "ready" if pending else "already_migrated")
        collection_rows.append({
            "id": coll.id, "name": coll.name, "linked_world_id": coll.shrecknet_world_id,
            "suggested_ontology_id": suggested, "source_count": sum(existing.values()),
            "pending_items": pending, "conflicting_items": conflicting,
            "existing_ontology_ids": sorted(k for k in existing if k is not None),
            "status": status,
        })
    tables = list(db.scalars(select(Table)))
    mapping_count = db.scalar(select(func.count()).select_from(LegacyTableMap)) or 0
    source_total = db.scalar(select(func.count()).select_from(ShreckWorldLibraryItem)) or 0
    collections_total = db.scalar(select(func.count()).select_from(SourceCollection)) or 0
    sessions_total = db.scalar(select(func.count()).select_from(TableSession)) or 0
    promotions = db.scalar(select(func.count()).select_from(SessionNarrativeSource)) or 0
    records = list(db.scalars(select(MigrationCenterAudit).order_by(
        MigrationCenterAudit.created_at.desc(), MigrationCenterAudit.id.desc()).limit(30)))
    world_rows = []
    for world in worlds:
        matches = [row for row in tables if row.world_id == world["id"]]
        wrong = sum(row.ontology_id != world["active_ontology_id"] for row in matches)
        rules = read_world_rules(db, world["id"])
        world_rows.append({**world, "table_count": len(matches), "table_scope_conflicts": wrong,
                          "rules_version_id": rules.version_id, "rules_conflict": rules.legacy_conflict})
    return {
        "mode": "read_only",
        "worlds": world_rows,
        "collections": collection_rows,
        "totals": {"legacy_collections": len(collections), "sources": source_total,
                   "world_source_collections": collections_total, "tables": len(tables),
                   "sessions": sessions_total, "legacy_table_id_mappings": mapping_count,
                   "published_session_narratives": promotions},
        "external_checks": [
            {"name": "Legacy ShreckRPG games, Chronicles, Notes and Activities",
             "status": "startup_migration_available",
             "detail": "Run the first-run startup option --migrate-shrecktables for a name-matched preview and explicit apply. Keep the original RPG database for Notes, Activities and other legacy game-ID references."},
            {"name": "Neo4j scenes and source anchors", "status": "operator_required",
             "detail": "Run the existing scene/source migration preview, then an explicit operator apply."},
            {"name": "Qdrant, embeddings, files and BookMaps", "status": "not_verified",
             "detail": "Data is not modified by this center; verify against deployed storage."},
            {"name": "Google Calendar and Foundry", "status": "not_verified",
             "detail": "No external calendar, Meet or Foundry changes are made here."},
            {"name": "Backups and rollback", "status": "not_verified",
             "detail": "An operator must take a consistent backup before any apply."},
        ],
        "history": [{"id": row.id, "actor_id": row.actor_id, "operation": row.operation,
                     "collection_id": row.collection_id, "target_ontology_id": row.target_ontology_id,
                     "changed_items": row.changed_items, "created_at": row.created_at.isoformat()}
                    for row in records],
    }


def apply_source_ownership(db: Session, token: str, *, collection_id: str, target: int,
                           fingerprint: str, actor_id: str) -> dict:
    # A second preview on this request prevents applying a stale client preview.
    result = _preview(db, token, collection_id, target)
    if result["blocked"]:
        raise HTTPException(409, "Conflicting source ownership must be resolved explicitly")
    if result["preview_fingerprint"] != fingerprint:
        raise HTTPException(409, "Migration preview has changed; refresh before applying")
    if not result["will_update"]:
        return {"changed_items": 0, "already_migrated": result["already_mapped"],
                "operation": "no_changes", "audit_id": None}
    pending_ids = [item.id for item in _sources(db, collection_id) if item.ontology_id is None]
    changed = db.execute(update(ShreckWorldLibraryItem).where(
        ShreckWorldLibraryItem.shreckworld_id == collection_id,
        ShreckWorldLibraryItem.ontology_id.is_(None),
    ).values(ontology_id=target)).rowcount
    if changed != result["will_update"]:
        db.rollback()
        raise HTTPException(409, "Source counts changed during migration; preview again")
    audit = MigrationCenterAudit(
        actor_id=actor_id, operation="assign_ontology_to_legacy_sources",
        collection_id=collection_id, target_ontology_id=target,
        changed_items=changed, preview_fingerprint=fingerprint,
        details={"target_world_id": result["target_world_id"],
                 "updated_source_ids": pending_ids},
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return {"changed_items": changed, "already_migrated": result["already_mapped"],
            "operation": audit.operation, "audit_id": audit.id}
