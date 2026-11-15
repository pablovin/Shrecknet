"""Architect Learn Ontology V5: world-scoped, evidence-bound discovery.

Each Bookmap window gets one model call. Complete, valid output parts are kept;
malformed entities, properties and relationships are skipped and reported in
the run activity log. Each source window is processed once; partial results do
not trigger source subdivision or a replacement model call.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4
from collections import Counter

from sqlalchemy import select, update
from app.celery_app import celery_app
from app.db.session import session_factory
from app.models.ontology_studio import OntologyLearnRun, OntologyLearnChapter, OntologyLearnWindow
from app.models.document import ShreckWorldDocument, ShreckWorldDocumentBookMap, ShreckWorldDocumentBlock
from app.models.shreckworld import ShreckWorldLibraryItem
from app.schemas.shreckworld import ShreckWorldModelTarget
from app.services.book_map_contract import read_book_map
from app.services.ontology_discovery_batches import build_discovery_batches
from app.services.ontology_compact import (
    CONTRACT_VERSION, DISCOVERY_SCHEMA, DISCOVERY_PROMPT,
    RECONCILIATION_SCHEMA,
    RECONCILIATION_PROMPT, CompactContractError, model_window, cache_fingerprint,
    parse_discovery, accumulate, ambiguous_groups, parse_discovery_response,
    parse_model_response,
    proposal_counts,
    reconciliation_input, apply_reconciliation,
)
from app.integrations.shreckllm import generate_shreckworld_structured_output
from app.services import ontology_studio
from app.services.ontology_run_reporting import progress, record_llm_usage

logger = logging.getLogger(__name__)

def model(run: OntologyLearnRun, stage: str) -> ShreckWorldModelTarget:
    return ShreckWorldModelTarget.model_validate(run.model_targets[stage])


def _chapter_sent(db, run: OntologyLearnRun, item, doc, batch) -> None:
    row = db.scalar(select(OntologyLearnChapter).where(
        OntologyLearnChapter.ontology_id == run.ontology_id,
        OntologyLearnChapter.source_id == item.id,
        OntologyLearnChapter.content_digest == doc.source_sha256,
        OntologyLearnChapter.chapter_id == batch["chapter_id"]))
    if row is None:
        db.add(OntologyLearnChapter(
            ontology_id=run.ontology_id, source_id=item.id, document_id=doc.id,
            content_digest=doc.source_sha256, chapter_id=batch["chapter_id"],
            chapter_title=batch["chapter_title"]))
    else:
        row.document_id = doc.id
        row.chapter_title = batch["chapter_title"]
        row.last_sent_at = datetime.now(timezone.utc)


def _prepare(db, run: OntologyLearnRun) -> list[tuple]:
    planned = []
    discovery_target = run.model_targets["discovery"]
    for source in run.input_sources:
        if not progress(db, run, 1, "preparing"):
            return []
        doc = db.get(ShreckWorldDocument, source["document_id"])
        item = db.get(ShreckWorldLibraryItem, source["source_id"])
        book = db.get(ShreckWorldDocumentBookMap, source["document_id"])
        if (not doc or not item or not book or not doc.is_active or
            item.source_type != "explanatory" or item.ontology_id != run.ontology_id or
            doc.library_item_id != item.id or doc.source_sha256 != source["content_digest"]):
            raise ValueError("pinned_source_unavailable")
        mapped = read_book_map(book.payload)
        blocks = {row.id: row for row in db.scalars(select(ShreckWorldDocumentBlock).where(
            ShreckWorldDocumentBlock.document_id == doc.id))}
        batches = build_discovery_batches(mapped, blocks)
        selected_chapters = source.get("chapter_ids")
        if selected_chapters is not None:
            if not set(selected_chapters) <= {b["chapter_id"] for b in batches}:
                raise ValueError("pinned_chapter_unavailable")
            batches = [b for b in batches if b["chapter_id"] in set(selected_chapters)]
        # Window numbers reset per chapter. This also supports stable
        # checkpoints when a chapter has multiple token-bounded windows.
        indices: dict[str, int] = {}
        for batch in batches:
            chapter = batch["chapter_id"]
            indices[chapter] = indices.get(chapter, 0) + 1
            content, lookup = model_window(batch, item.title)
            fingerprint = cache_fingerprint(content, discovery_target)
            row = OntologyLearnWindow(
                run_id=run.id, ontology_id=run.ontology_id, source_id=item.id,
                document_id=doc.id, content_digest=doc.source_sha256,
                chapter_id=chapter, chapter_title=batch["chapter_title"],
                window_index=indices[chapter], fingerprint=fingerprint,
                contract_version=CONTRACT_VERSION, status="pending",
            )
            db.add(row)
            planned.append((item, doc, batch, content, lookup, row))
    db.commit()
    return planned


def _reusable_window(db, row: OntologyLearnWindow) -> OntologyLearnWindow | None:
    return db.scalar(select(OntologyLearnWindow).where(
        OntologyLearnWindow.ontology_id == row.ontology_id,
        OntologyLearnWindow.source_id == row.source_id,
        OntologyLearnWindow.content_digest == row.content_digest,
        OntologyLearnWindow.fingerprint == row.fingerprint,
        OntologyLearnWindow.contract_version == CONTRACT_VERSION,
        OntologyLearnWindow.status == "completed",
        OntologyLearnWindow.id != row.id,
    ).order_by(OntologyLearnWindow.finished_at.desc()).limit(1))


def _window_extract(db, run: OntologyLearnRun, item, doc, batch, content: str,
                    lookup: dict, row: OntologyLearnWindow,
                    stats: dict) -> list[dict]:
    cached = _reusable_window(db, row)
    if cached is not None:
        row.proposals = cached.proposals
        row.status = "completed"
        row.cached = True
        row.validation_diagnostics = cached.validation_diagnostics or []
        row.finished_at = datetime.now(timezone.utc)
        db.commit()
        stats.update(proposal_counts(cached.proposals or []))
        stats["metadata_warnings"] = sum(
            len(p.get("validation_warnings") or []) for p in cached.proposals or []
        )
        stats["malformed_parts"] = list(row.validation_diagnostics)
        stats["cached"] = True
        return cached.proposals
    row.status = "running"
    db.commit()
    try:
        response = generate_shreckworld_structured_output(
            usage_tag="architect_ontology_discovery", system_prompt=DISCOVERY_PROMPT,
            user_content=content, schema_name="architect_ontology_schema_v5",
            response_schema=DISCOVERY_SCHEMA, max_tokens=8000,
            model_target=model(run, "discovery"))
        row.input_tokens = max(0, response.input_tokens or 0)
        row.output_tokens = max(0, response.output_tokens or 0)
        record_llm_usage(db, run, input_tokens=response.input_tokens,
                         output_tokens=response.output_tokens)
        data = parse_discovery_response(response, stats=stats)
        base = {
            "source_id": item.id, "document_id": doc.id,
            "content_digest": doc.source_sha256, "chapter_id": batch["chapter_id"],
            "chapter_title": batch["chapter_title"],
        }
        proposals = parse_discovery(data, lookup, base, stats=stats)
        row.validation_diagnostics = list(stats.get("malformed_parts", []))[:100]
        row.proposals = proposals
        row.status = "completed"
        row.finished_at = datetime.now(timezone.utc)
        db.commit()
        return proposals
    except Exception as exc:
        db.rollback()
        checkpoint = db.get(OntologyLearnWindow, row.id)
        checkpoint.status = "failed"
        checkpoint.error_code = (
            exc.code if isinstance(exc, CompactContractError) else "model_request_failed"
        )
        checkpoint.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise


def _reconcile(db, run: OntologyLearnRun, found: dict) -> list[dict] | None:
    candidates = list(found.values())
    approved = ontology_studio.graph(db, run.ontology_id)
    entity_names = {row["id"]: row["name"] for row in approved["entities"]}
    known = [{"kind": "entity", "name": row["name"]} for row in approved["entities"]]
    known.extend({"kind": "property", "name": row["name"],
                  "semantic_type": row.get("semantic_type"),
                  "entity": entity_names.get(row["entity_id"], "")}
                 for row in approved["properties"])
    known.extend({"kind": "relationship", "name": row["name"],
                  "source": entity_names.get(row["source_id"], ""),
                  "target": entity_names.get(row["target_id"], "")}
                 for row in approved["relationships"])
    groups = ambiguous_groups(candidates, known=known)
    discarded: set[int] = set()
    if not progress(db, run, 75, "reconciling", {
        "event": "reconciliation_started", "total_groups": len(groups),
        "proposals_discovered": len(candidates),
    }):
        return None
    for position, indices in enumerate(groups):
        if not progress(db, run, 75 + int(19 * position / len(groups)), "reconciling", {
            "event": "reconciliation_group_started",
            "group_index": position + 1, "total_groups": len(groups),
            "proposals_discovered": len(candidates),
        }):
            return None
        response = generate_shreckworld_structured_output(
            usage_tag="architect_ontology_reconciliation",
            system_prompt=RECONCILIATION_PROMPT,
            user_content=reconciliation_input(candidates, indices, known=known),
            schema_name="architect_ontology_reconcile_ops_v2",
            response_schema=RECONCILIATION_SCHEMA,
            max_tokens=900, model_target=model(run, "reconciliation"))
        record_llm_usage(db, run, input_tokens=response.input_tokens,
                         output_tokens=response.output_tokens)
        data = parse_model_response(response, stage="reconciliation")
        discarded.update(apply_reconciliation(candidates, indices, data))
        if not progress(db, run, 75 + int(19 * (position + 1) / len(groups)), "reconciling", {
            "event": "reconciliation_group_completed", "group_index": position + 1,
            "total_groups": len(groups),
        }):
            return None
    # Merged/renamed entities may produce duplicate dependent proposals. Combine
    # again deterministically so their evidence remains complete.
    final: dict = {}
    for index, candidate in enumerate(candidates):
        if index not in discarded:
            accumulate(final, candidate)
    return list(final.values())


def _change_labels(db, run: OntologyLearnRun, candidates: list[dict]) -> list[dict]:
    current = ontology_studio.graph(db, run.ontology_id)
    names = {e["id"]: e["name"].casefold() for e in current["entities"]}
    entities = {e["name"].casefold() for e in current["entities"]}
    properties = {
        (names.get(p["entity_id"]), p["name"].casefold()): p
        for p in current["properties"]
    }
    relationships = {
        (names.get(r["source_id"]), names.get(r["target_id"]), r["name"].casefold())
        for r in current["relationships"]
    }
    for item in candidates:
        kind = item["kind"]
        if kind == "entity":
            item["change"] = "update" if item["name"].casefold() in entities else "new"
        elif kind == "property":
            key = (item["entity"].casefold(), item["name"].casefold())
            old = properties.get(key)
            item["change"] = ("new" if old is None else "conflict"
                              if old["data_type"] != item["data_type"] else "update")
            if old is not None and old["data_type"] != item["data_type"]:
                item["conflict"] = {"reason": "existing_property_type_mismatch"}
            elif old is not None and old.get("semantic_type") and item.get("semantic_type") and (
                old["semantic_type"] != item["semantic_type"]
            ):
                item["conflict"] = {"reason": "existing_property_semantic_mismatch"}
        else:
            key = (item["source"].casefold(), item["target"].casefold(), item["name"].casefold())
            item["change"] = "update" if key in relationships else "new"
        if item.get("conflict"):
            item["change"] = "conflict"
        item["id"] = str(uuid4())
    return candidates


@celery_app.task(name="shreckworld.architect.learn_ontology")
def run_ontology_discovery(run_id: str) -> None:
    with session_factory()() as db:
        claim = db.execute(update(OntologyLearnRun).where(
            OntologyLearnRun.id == run_id, OntologyLearnRun.status == "queued",
        ).values(status="running", stage="preparing", progress=1))
        if claim.rowcount != 1:
            db.rollback()
            return
        db.commit()
        try:
            run = db.get(OntologyLearnRun, run_id)
            planned = _prepare(db, run)
            db.refresh(run)
            if run.status == "cancelled":
                return
            found: dict = {}
            total = len(planned)
            remaining_by_chapter = Counter((row.source_id, row.chapter_id)
                                           for *_, row in planned)
            completed = 0
            if not progress(db, run, 5, "preparing", {
                "event": "plan_ready", "total_sources": len(run.input_sources),
                "total_windows": total, "completed_windows": 0,
                "proposals_discovered": 0,
                "contract_version": CONTRACT_VERSION,
            }):
                return
            for item, doc, batch, content, lookup, checkpoint in planned:
                detail = {
                    "source_id": item.id, "source_title": item.title,
                    "chapter_id": batch["chapter_id"], "chapter_title": batch["chapter_title"],
                    "window_index": checkpoint.window_index, "total_windows": total,
                    "completed_windows": completed, "proposals_discovered": len(found),
                    "contract_version": CONTRACT_VERSION,
                }
                if not progress(db, run, 5 + int(65 * completed / max(1, total)),
                                "discovering", {"event": "window_started", **detail}):
                    return
                window_stats: dict = {}
                discovered = _window_extract(db, run, item, doc, batch,
                                             content, lookup, checkpoint, window_stats)
                for candidate in discovered:
                    accumulate(found, candidate)
                completed += 1
                chapter_key = (checkpoint.source_id, checkpoint.chapter_id)
                remaining_by_chapter[chapter_key] -= 1
                if remaining_by_chapter[chapter_key] == 0:
                    # A chapter is "sent" only after every one of its bounded
                    # windows produced a valid, persisted extraction.
                    _chapter_sent(db, run, item, doc, batch)
                    db.commit()
                # Distinguish reusable schema elements from total proposal count.
                counts = proposal_counts(found.values())
                if not progress(db, run, 5 + int(65 * completed / max(1, total)),
                                "discovering", {
                                    "event": "window_completed", **detail,
                                    "completed_windows": completed, "proposals_discovered": len(found),
                                    **counts,
                                    "window_suppressed_instances": window_stats.get("suppressed_instances", 0),
                                    "window_unresolved_relationships": window_stats.get("unresolved_relationships", 0),
                                    "window_metadata_warnings": window_stats.get("metadata_warnings", 0),
                                    "malformed_parts_count": len(window_stats.get("malformed_parts", [])),
                                    "malformed_parts": window_stats.get("malformed_parts", [])[:30],
                                    "cached": bool(checkpoint.cached),
                                    "window_input_tokens": checkpoint.input_tokens,
                                    "window_output_tokens": checkpoint.output_tokens,
                                }):
                    return
            proposals = _reconcile(db, run, found)
            if proposals is None:
                return
            if not progress(db, run, 95, "persisting", {
                "event": "proposals_ready", "proposals_discovered": len(proposals),
                **proposal_counts(proposals),
                "metadata_warnings": sum(
                    len(p.get("validation_warnings") or []) for p in proposals
                ),
                "completed_windows": completed, "total_windows": total,
            }):
                return
            run.proposals = _change_labels(db, run, proposals)
            if not progress(db, run, 99, "persisting"):
                return
            run.progress, run.stage, run.status = 100, "awaiting_review", "awaiting_review"
            run.diagnostics = [*(run.diagnostics or []), {
                "kind": "progress", "event": "awaiting_review",
                "stage": "awaiting_review", "progress": 100,
                "proposals_discovered": len(proposals),
                **proposal_counts(proposals),
                "completed_windows": completed, "total_windows": total,
                "at": datetime.now(timezone.utc).isoformat()}][-250:]
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.exception("Architect ontology V5 failed for run %s", run_id)
            run = db.get(OntologyLearnRun, run_id)
            if run:
                run.status, run.stage = "failed", "failed"
                run.diagnostics = [*(run.diagnostics or []), {
                    "kind": "error",
                    "code": exc.code if isinstance(exc, CompactContractError) else "ontology_discovery_failed",
                    "failure_type": type(exc).__name__,
                    "at": datetime.now(timezone.utc).isoformat()}][-250:]
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
            raise
