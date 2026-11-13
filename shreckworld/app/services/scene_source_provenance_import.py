"""Idempotent, evidence-based Scene -> ShreckWorld source provenance backfill.

This job runs after the Narrative Source importer on ShreckWorld API startup.
Only writes the optional Scene.source_provenance_json property in Neo4j.
DERIVED_FROM edges and original Scene/Milestone content remain untouched.
"""
from __future__ import annotations

from collections import Counter
import fcntl
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any

from neo4j import GraphDatabase
from sqlalchemy import select

from app.core.config import get_settings
from app.models.sources import GraphSourceImport, SourceAsset

logger = logging.getLogger(__name__)
_MAX_DETAILS_BYTES = 24 * 1024 * 1024
_BATCH_SIZE = 300


def _paragraphs_from_proposal(proposal: dict[str, Any]) -> dict[str, Any] | None:
    """Accept only explicit, internally consistent Architect paragraph IDs."""
    raw = proposal.get("source_paragraphs_absolute") or []
    try:
        ids = [int(n) for n in raw] if isinstance(raw, list) else []
        if not ids or ids != sorted(set(ids)) or ids[0] <= 0:
            # Older records may have a range but no explicit paragraph list.
            start = int(proposal.get("start_paragraph") or 0)
            end = int(proposal.get("end_paragraph") or 0)
            if not (1 <= start <= end):
                return None
            ids = []
        else:
            start = int(proposal.get("start_paragraph") or ids[0])
            end = int(proposal.get("end_paragraph") or ids[-1])
            if not (1 <= start <= ids[0] <= ids[-1] <= end):
                return None
        if isinstance(raw, list) and len(raw) > 10000:
            return None
        if end - start > 100_000:
            return None
        return {"start_paragraph": start, "end_paragraph": end,
                "source_paragraphs_absolute": ids}
    except (TypeError, ValueError, OverflowError):
        return None


def _historical_scene_ranges(path: Path) -> tuple[dict[tuple[str, str, str], dict], int]:
    """Recover exact Architect identities from live jobs, durable archives, and artifacts.

    Background jobs may be removed by Shrecknet startup cleanup. The Architect
    pipeline also writes persistent pipeline_output.json files in local_tests.
    Only exact instance + scene ID + original entity ID matches are accepted.
    """
    unique: dict[tuple[str, str, str], dict] = {}
    conflicting: set[tuple[str, str, str]] = set()
    scanned = 0

    def add_pipeline(pipeline: object, extra_instance: str = "") -> None:
        nonlocal scanned
        if not isinstance(pipeline, dict):
            return
        instance = str(pipeline.get("ontology_instance_id") or extra_instance or "").strip()
        outputs = pipeline.get("outputs") or {}
        scenes = outputs.get("scenes") or outputs.get("scene_proposals") or []
        if not instance or not isinstance(scenes, list):
            return
        scanned += 1
        for proposal in scenes:
            if not isinstance(proposal, dict):
                continue
            scene_id = str(proposal.get("scene_id") or "").strip()
            derived = proposal.get("derived_from") or {}
            entity_id = str(proposal.get("source_entity_instance_id") or (
                derived.get("entity_instance_id") if isinstance(derived, dict) else ""
            ) or "").strip()
            if not scene_id or not entity_id:
                continue
            span = _paragraphs_from_proposal(proposal)
            if span is None:
                continue
            key = (instance, scene_id, entity_id)
            if key in conflicting:
                continue
            if key in unique and unique[key] != span:
                unique.pop(key, None)
                conflicting.add(key)
            else:
                unique[key] = span

    def load_json(raw: str) -> None:
        try:
            details = json.loads(raw)
            if not isinstance(details, dict):
                return
            pipeline = details.get("pipeline_output") or details
            if isinstance(pipeline, str):
                pipeline = json.loads(pipeline)
            add_pipeline(pipeline, str(details.get("ontology_instance_id") or ""))
        except (ValueError, TypeError):
            return

    if path.is_file():
        conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='background_jobs'").fetchone():
                for row in conn.execute("""
                    SELECT details FROM background_jobs
                    WHERE job_type = 'architect_analysis' AND details IS NOT NULL
                    ORDER BY id DESC
                """):
                    raw = row["details"]
                    if raw and len(raw) <= _MAX_DETAILS_BYTES:
                        load_json(raw)
        finally:
            conn.close()

    # Preserved by Shrecknet's startup job cleanup.
    archived_dir = path.parent / "architect_provenance"
    if archived_dir.is_dir():
        for artifact in archived_dir.glob("analysis-job-*.json"):
            try:
                if artifact.stat().st_size <= _MAX_DETAILS_BYTES:
                    load_json(artifact.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                logger.warning("Could not read historical Architect evidence artifact")

    # Existing Architect analysis artifacts predate the safe cleanup archive.
    # The pipeline uses the historical 'arhictect' spelling on disk.
    artifacts_dir = path.parent / "local_tests" / "arhictect" / "Analyses"
    if artifacts_dir.is_dir():
        for artifact in artifacts_dir.glob("*/pipeline_output.json"):
            try:
                if artifact.stat().st_size <= _MAX_DETAILS_BYTES:
                    load_json(artifact.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                logger.warning("Could not read Architect pipeline output artifact")

    if conflicting:
        logger.warning("Skipping %d ambiguous Architect paragraph matches", len(conflicting))
    return unique, scanned


def _source_mappings(session_factory) -> dict[tuple[int, str, str], tuple[str, str, str | None]]:
    """Join imported graph IDs with per-section Markdown asset pointers."""
    with session_factory() as db:
        assets = {
            (row.source_id, row.section_id): row.id
            for row in db.scalars(select(SourceAsset).where(SourceAsset.kind == "markdown"))
        }
        return {
            (int(row.ontology_id), row.instance_id, row.entity_instance_id):
                (row.source_id, row.section_id, assets.get((row.source_id, row.section_id)))
            for row in db.scalars(select(GraphSourceImport))
        }


def _decode(raw: object) -> dict | None:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        result = raw
    elif isinstance(raw, str):
        try:
            result = json.loads(raw)
        except (ValueError, TypeError):
            return None
    else:
        return None
    return result if isinstance(result, dict) else None


def _reconciled_payload(
    *,
    previous: object,
    entity_id: str,
    document: tuple[str, str, str | None],
    historical: dict | None,
) -> tuple[dict | None, bool]:
    """Never overwrite existing document or paragraph anchors with guessed data."""
    existing = _decode(previous)
    if existing is None:
        return None, False
    source_id, section_id, markdown_asset_id = document
    if existing.get("entity_instance_id") not in (None, entity_id):
        return None, False
    for key, expected in (("source_id", source_id), ("section_id", section_id)):
        if existing.get(key) not in (None, "", expected):
            return None, False
    if existing.get("markdown_asset_id") not in (None, "", markdown_asset_id):
        # Old source asset might have been replaced; do not change a manual ref.
        return None, False
    result = dict(existing)
    result["entity_instance_id"] = entity_id
    result["source_id"] = source_id
    result["section_id"] = section_id
    if markdown_asset_id:
        result["markdown_asset_id"] = markdown_asset_id
    has_paragraphs = (result.get("start_paragraph") is not None and
                      result.get("end_paragraph") is not None)
    historical_added = False
    if not has_paragraphs and historical:
        for key in ("start_paragraph", "end_paragraph", "source_paragraphs_absolute"):
            result[key] = historical[key]
        result["provenance_status"] = "historical_unverified"
        historical_added = True
    elif not has_paragraphs:
        result["provenance_status"] = result.get("provenance_status") or "document_only"
    return result, historical_added


def _write_batch(tx, batch: list[dict]) -> None:
    tx.run("""
        UNWIND $records AS record
        MATCH (page:OntologyInstance {instance_id: record.instance_id})
              -[:HAS_SCENE]->(scene:Scene {id: record.scene_id})
              -[:DERIVED_FROM]->(entity:EntityInstance {entity_instance_id: record.entity_id})
        WHERE toInteger(page.ontology_id) = record.ontology_id
          AND toString(scene.instance_id) = record.instance_id
        SET scene.source_provenance_json = record.payload,
            scene.source_id = record.source_id,
            scene.source_section_id = record.section_id,
            scene.source_markdown_asset_id = record.markdown_asset_id
    """, records=batch).consume()


def _reconcile(session_factory, settings) -> dict[str, int]:
    mappings = _source_mappings(session_factory)
    historical, jobs = _historical_scene_ranges(settings.legacy_jobs_database_path)
    stats = Counter({"mappings": len(mappings), "jobs_scanned": jobs,
                     "scenes_checked": 0, "updated": 0, "paragraphs_recovered": 0,
                     "document_only": 0, "conflicts": 0})
    driver = GraphDatabase.driver(settings.graph_neo4j_uri,
                                  auth=(settings.graph_neo4j_user, settings.graph_neo4j_password),
                                  connection_timeout=5)
    try:
        with driver.session(database=settings.graph_neo4j_database) as graph:
            rows = graph.run("""
                MATCH (page:OntologyInstance)-[:HAS_SCENE]->(scene:Scene)
                      -[:DERIVED_FROM]->(source:EntityInstance)<-[:HAS_ENTITY]-(page)
                WHERE toString(scene.instance_id) = toString(page.instance_id)
                RETURN toInteger(page.ontology_id) AS ontology_id,
                       page.instance_id AS instance_id,
                       scene.id AS scene_id,
                       source.entity_instance_id AS entity_id,
                       scene.source_provenance_json AS existing
            """).data()
            # Exhaust the read cursor before starting any write transaction.
            batch = []
            seen: set[tuple[str, str]] = set()
            for row in rows:
                if row["ontology_id"] is None or not row["scene_id"]:
                    continue
                identity = (str(row["instance_id"]), str(row["scene_id"]))
                if identity in seen:
                    stats["conflicts"] += 1
                    continue
                seen.add(identity)
                key = (int(row["ontology_id"]), identity[0], str(row["entity_id"]))
                mapping = mappings.get(key)
                if mapping is None:
                    continue
                stats["scenes_checked"] += 1
                payload, recovered = _reconciled_payload(
                    previous=row["existing"], entity_id=key[2], document=mapping,
                    historical=historical.get((identity[0], identity[1], key[2])))
                if payload is None:
                    stats["conflicts"] += 1
                    continue
                if payload.get("start_paragraph") is None:
                    stats["document_only"] += 1
                if recovered:
                    stats["paragraphs_recovered"] += 1
                if _decode(row["existing"]) == payload:
                    continue
                batch.append({"ontology_id": key[0], "instance_id": identity[0],
                              "scene_id": identity[1], "entity_id": key[2],
                              "payload": json.dumps(payload, ensure_ascii=False),
                              "source_id": payload.get("source_id"),
                              "section_id": payload.get("section_id"),
                              "markdown_asset_id": payload.get("markdown_asset_id")})
                if len(batch) >= _BATCH_SIZE:
                    graph.execute_write(_write_batch, batch)
                    stats["updated"] += len(batch)
                    batch = []
            if batch:
                graph.execute_write(_write_batch, batch)
                stats["updated"] += len(batch)
    finally:
        driver.close()
    output = dict(stats)
    logger.info("Scene source provenance startup reconciliation: %s", output)
    return output


def import_scene_source_provenance(session_factory, settings=None) -> dict[str, int]:
    """Run once per backend startup; reruns are safe and never erase manual anchors."""
    settings = settings or get_settings()
    settings.media_root.mkdir(parents=True, exist_ok=True)
    lock_path = settings.media_root / ".scene-source-provenance.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("Scene provenance migration already running in another process")
            return {"updated": 0, "paragraphs_recovered": 0, "document_only": 0, "conflicts": 0}
        try:
            return _reconcile(session_factory, settings)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
