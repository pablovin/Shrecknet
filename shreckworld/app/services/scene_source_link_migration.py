"""Manually reconcile Scene and Milestone references to imported ShreckWorld Sources.

The migration follows the legacy Scene/Milestone DERIVED_FROM entity to the
deterministic narrative collection entry produced by graph_source_import, then
copies a verified, same-ontology Source ID onto the graph node. Legacy graph
relationships are never changed. The operation is read-only unless apply=True.
"""
from __future__ import annotations

from collections import defaultdict
import json
import logging
from typing import Any

from neo4j import GraphDatabase
from sqlalchemy import select

from app.core.config import get_settings
from app.models.shreckworld import ShreckWorldLibraryItem
from app.models.source_collection import SourceCollection
from app.models.sources import SourceAsset
from app.services.graph_source_import import graph_collection_entry_id
from app.services.scene_source_provenance_import import _historical_scene_ranges

logger = logging.getLogger(__name__)
_BATCH_SIZE = 300


def _source_references(session_factory) -> dict[tuple[int, str], list[dict[str, Any]]]:
    """Resolve collection entry IDs to valid Source IDs and current sections."""
    with session_factory() as db:
        library_items = {
            item.id: item
            for item in db.scalars(select(ShreckWorldLibraryItem))
        }
        collections = db.scalars(select(SourceCollection).where(
            SourceCollection.source_type == "narrative",
            SourceCollection.archived.is_(False),
        )).all()
        db_assets = db.scalars(select(SourceAsset).where(SourceAsset.kind == "markdown")).all()
        assets_by_source: dict[str, list[str]] = defaultdict(list)
        for asset in db_assets:
            assets_by_source[asset.source_id].append(asset.section_id)
    references: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for collection in collections:
        entries = collection.nodes or []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            entry_id = str(entry["id"])
            for source_id in entry.get("source_ids") or []:
                item = library_items.get(str(source_id))
                if item is None or item.ontology_id != collection.ontology_id:
                    continue
                section_ids = sorted(set(assets_by_source.get(item.id, [])))
                section_id = section_ids[0] if len(section_ids) == 1 else None
                markdown_asset_id = next((
                    asset.id for asset in db_assets
                    if asset.source_id == item.id and asset.section_id == section_id
                ), None) if section_id else None
                references[(int(collection.ontology_id), entry_id)].append({
                    "source_id": item.id,
                    "source_title": item.title,
                    "section_id": section_id,
                    "markdown_asset_id": markdown_asset_id,
                    "section_ambiguous": len(section_ids) > 1,
                })
    return references


def _candidate_source(
    references: dict[tuple[int, str], list[dict[str, Any]]],
    ontology_id: int,
    entry_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    candidates = references.get((ontology_id, entry_id), [])
    unique = {item["source_id"]: item for item in candidates}
    if len(unique) == 1:
        return next(iter(unique.values())), None
    if not unique:
        return None, "no_existing_source_for_collection_entry"
    return None, "multiple_sources_for_collection_entry"


def _payload_with_source(
    previous: object,
    *,
    entity_id: str,
    source: dict[str, Any],
    historical: dict[str, Any] | None = None,
) -> str:
    """Repair Source identity while preserving any existing passage evidence."""
    try:
        payload = json.loads(previous) if isinstance(previous, str) else previous
    except (TypeError, ValueError):
        payload = None
    if not isinstance(payload, dict):
        payload = {}
    previous_source_id = payload.get("source_id")
    payload["entity_instance_id"] = entity_id
    payload["source_id"] = source["source_id"]
    if previous_source_id not in (None, source["source_id"]):
        payload.pop("source_revision", None)
    if source.get("section_id"):
        payload["section_id"] = source["section_id"]
    else:
        payload.pop("section_id", None)
    if source.get("markdown_asset_id"):
        payload["markdown_asset_id"] = source["markdown_asset_id"]
    else:
        payload.pop("markdown_asset_id", None)
    start = payload.get("start_paragraph")
    end = payload.get("end_paragraph")
    valid_range = (
        isinstance(start, int) and not isinstance(start, bool)
        and isinstance(end, int) and not isinstance(end, bool)
        and 1 <= start <= end
    )
    paragraphs = payload.get("source_paragraphs_absolute")
    if paragraphs is not None and (
        not isinstance(paragraphs, list)
        or any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in paragraphs)
        or paragraphs != sorted(set(paragraphs))
    ):
        payload.pop("source_paragraphs_absolute", None)
    if not valid_range:
        payload.pop("start_paragraph", None)
        payload.pop("end_paragraph", None)
        if historical:
            payload.update({
                "start_paragraph": historical["start_paragraph"],
                "end_paragraph": historical["end_paragraph"],
                "source_paragraphs_absolute": historical["source_paragraphs_absolute"],
                "provenance_status": "historical_unverified",
            })
        else:
            payload.setdefault("provenance_status", "document_only")
    return json.dumps(payload, ensure_ascii=False)


def _load_targets(driver, database: str) -> list[dict[str, Any]]:
    with driver.session(database=database) as graph:
        scenes = graph.run("""
            MATCH (owner:OntologyInstance)-[:HAS_SCENE]->(scene:Scene)
            WHERE toString(owner.instance_id) = toString(scene.instance_id)
            OPTIONAL MATCH (scene)-[:DERIVED_FROM]->(entity:EntityInstance)
            RETURN toInteger(owner.ontology_id) AS ontology_id, owner.instance_id AS instance_id,
                   scene.id AS id, 'Scene' AS kind,
                   collect(DISTINCT entity.entity_instance_id) AS entity_ids,
                   scene.source_id AS source_id, scene.source_section_id AS section_id,
                   scene.source_markdown_asset_id AS markdown_asset_id,
                   scene.source_provenance_json AS provenance
        """).data()
        milestones = graph.run("""
            MATCH (owner:OntologyInstance)-[:HAS_SCENE]->(scene:Scene)-[:CONTAINS]->(milestone:Milestone)
            WHERE toString(owner.instance_id) = toString(scene.instance_id)
            OPTIONAL MATCH (milestone)-[:DERIVED_FROM]->(entity:EntityInstance)
            RETURN toInteger(owner.ontology_id) AS ontology_id, owner.instance_id AS instance_id,
                   scene.id AS parent_scene_id, milestone.id AS id, 'Milestone' AS kind,
                   collect(DISTINCT entity.entity_instance_id) AS entity_ids,
                   milestone.source_id AS source_id, milestone.source_section_id AS section_id,
                   milestone.source_markdown_asset_id AS markdown_asset_id,
                   null AS provenance
        """).data()
    return [dict(row) for row in scenes + milestones]


def _write_target_batch(tx, records: list[dict[str, Any]]) -> None:
    scenes = [r for r in records if r["kind"] == "Scene"]
    milestones = [r for r in records if r["kind"] == "Milestone"]
    if scenes:
        result = tx.run("""
        UNWIND $records AS record
        MATCH (node:Scene {id: record.id, instance_id: record.instance_id})
              -[:DERIVED_FROM]->(:EntityInstance {entity_instance_id: record.entity_id})
        WHERE toInteger(node.ontology_id) = record.ontology_id
        SET node.source_id = record.source_id,
            node.source_section_id = record.section_id,
            node.source_markdown_asset_id = record.markdown_asset_id,
            node.source_provenance_json = record.provenance
        RETURN count(DISTINCT node) AS updated
        """, records=scenes).single()
        if int(result["updated"] or 0) != len(scenes):
            raise RuntimeError("Scene anchors changed during Source-reference migration")
    if milestones:
        result = tx.run("""
        UNWIND $records AS record
        MATCH (node:Milestone {
            id: record.id, scene_id: record.parent_scene_id,
            instance_id: record.instance_id
        })-[:DERIVED_FROM]->(:EntityInstance {entity_instance_id: record.entity_id})
        WHERE toInteger(node.ontology_id) = record.ontology_id
        SET node.source_id = record.source_id,
            node.source_section_id = record.section_id,
            node.source_markdown_asset_id = record.markdown_asset_id
        RETURN count(DISTINCT node) AS updated
        """, records=milestones).single()
        if int(result["updated"] or 0) != len(milestones):
            raise RuntimeError("Milestone anchors changed during Source-reference migration")


def migrate_scene_and_milestone_sources(
    session_factory,
    settings=None,
    *,
    apply: bool = False,
    source_import_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preview or apply direct Source ID references; preserve all graph edges."""
    settings = settings or get_settings()
    references = _source_references(session_factory)
    historical, artifacts_scanned = _historical_scene_ranges(settings.legacy_jobs_database_path)
    driver = GraphDatabase.driver(
        settings.graph_neo4j_uri,
        auth=(settings.graph_neo4j_user, settings.graph_neo4j_password),
        connection_timeout=5,
    )
    try:
        targets = _load_targets(driver, settings.graph_neo4j_database)
        with driver.session(database=settings.graph_neo4j_database) as graph:
            ontologies = [dict(row) for row in graph.run(
                "MATCH (ontology:Ontology) RETURN toInteger(ontology.ontology_id) AS ontology_id, "
                "ontology.name AS name ORDER BY ontology_id"
            )]
        report: dict[str, Any] = {
            "mode": "apply" if apply else "dry_run",
            "ontologies": ontologies,
            "source_import": source_import_summary or {},
            "historical_artifacts_scanned": artifacts_scanned,
            "sources_added": [],
            "scenes": [],
            "milestones": [],
        }
        changes: list[dict[str, Any]] = []
        for target in targets:
            kind = str(target["kind"]).lower() + "s"
            base = {"ontology_id": target.get("ontology_id"),
                    "instance_id": target.get("instance_id"),
                    f"{target['kind'].lower()}_id": target.get("id")}
            entity_ids = sorted({str(x) for x in (target.get("entity_ids") or []) if x})
            if len(entity_ids) != 1 or target.get("ontology_id") is None:
                report[kind].append(base | {
                    "status": "not_linked",
                    "reason": "missing_or_ambiguous_derived_from_entity",
                    "source_entity_ids": entity_ids,
                })
                continue
            entity_id = entity_ids[0]
            entry_id = graph_collection_entry_id(int(target["ontology_id"]), entity_id)
            # Resolve by the deterministic importer-owned entry key, not by a
            # lossy alias/title comparison.
            source, reason = _candidate_source(references, int(target["ontology_id"]), entry_id)
            if source is None:
                report[kind].append(base | {
                    "status": "not_linked", "reason": reason,
                    "source_entity_id": entity_id,
                })
                continue
            historical_range = historical.get((
                str(target.get("instance_id") or ""), str(target.get("id") or ""), entity_id,
            ))
            payload = _payload_with_source(
                target.get("provenance"), entity_id=entity_id, source=source,
                historical=historical_range,
            ) if target["kind"] == "Scene" else None
            provenance_status = json.loads(payload).get("provenance_status") if payload else None
            record = {
                "id": target["id"], "kind": target["kind"],
                "ontology_id": int(target["ontology_id"]),
                "instance_id": str(target["instance_id"]),
                "parent_scene_id": target.get("parent_scene_id"),
                "entity_id": entity_id,
                "source_id": source["source_id"], "section_id": source.get("section_id"),
                "markdown_asset_id": source.get("markdown_asset_id"),
                "provenance": payload,
            }
            current = (target.get("source_id") == record["source_id"]
                       and target.get("section_id") == record["section_id"]
                       and target.get("markdown_asset_id") == record["markdown_asset_id"]
                       and (target["kind"] != "Scene" or target.get("provenance") == payload))
            status = "already_linked" if current else ("linked" if apply else "would_link")
            report[kind].append(base | {
                "status": status, "source_entity_id": entity_id,
                "source_id": source["source_id"], "source_title": source["source_title"],
                "section_id": source.get("section_id"),
                "provenance_status": provenance_status,
            })
            if apply and not current:
                changes.append(record)
        if apply and changes:
            for offset in range(0, len(changes), _BATCH_SIZE):
                with driver.session(database=settings.graph_neo4j_database) as graph:
                    graph.execute_write(_write_target_batch, changes[offset:offset + _BATCH_SIZE])
        # Summaries are kept alongside the per-record audit lists.
        for kind in ("scenes", "milestones"):
            records = report[kind]
            report[kind + "_summary"] = {
                "total": len(records),
                "linked": sum(row["status"] in {"linked", "already_linked"} for row in records),
                "would_link": sum(row["status"] == "would_link" for row in records),
                "not_linked": sum(row["status"] == "not_linked" for row in records),
                "already_linked": sum(row["status"] == "already_linked" for row in records),
            }
            per_ontology: dict[int, dict[str, int]] = {}
            for row in records:
                ontology_id = row.get("ontology_id")
                if ontology_id is None:
                    continue
                group = per_ontology.setdefault(int(ontology_id), {
                    "total": 0, "linked": 0, "would_link": 0, "not_linked": 0,
                    "already_linked": 0,
                })
                group["total"] += 1
                if row["status"] == "linked":
                    group["linked"] += 1
                elif row["status"] == "already_linked":
                    group["linked"] += 1
                    group["already_linked"] += 1
                elif row["status"] in {"would_link", "not_linked"}:
                    group[row["status"]] += 1
            report[kind + "_summary_by_ontology"] = [
                {"ontology_id": oid, **per_ontology[oid]}
                for oid in sorted(per_ontology)
            ]
        logger.info("Scene and milestone Source link migration report: %s", report)
        return report
    finally:
        driver.close()


