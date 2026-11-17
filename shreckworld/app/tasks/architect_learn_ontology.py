"""Architect V6: sequential, evidence-bound chapter conversations.

The service owns stage contracts and canonical reconciliation; this task owns
authorization-pinned inputs, checkpoints, cancellation and persisted job reporting.
"""
from __future__ import annotations

import json
import logging
import hashlib
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
    CONTRACT_VERSION, CompactContractError, model_window, cache_fingerprint,
    accumulate, compact_json, proposal_counts,
)
from app.services.ontology_sequential import (
    SYSTEM_PROMPT, STAGES, DUPLICATE_PROMPT, DUPLICATE_SCHEMA, DiscoveryCancelled,
    discover_chapter, graph_registry, reconcile, registry_payload,
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
        # V6 stores one complete chapter in each window checkpoint.
        # Numbering remains compatible with historical window records.
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


def _ask(db, run, row=None):
    def ask(stage, prompt, content, schema, messages):
        if not progress(db, run, run.progress, "discovering" if stage != "reconciliation" else "reconciling",
                        {"event": "stage_started", "discovery_stage": stage,
                         **({"chapter_id": row.chapter_id} if row is not None else {})}):
            raise DiscoveryCancelled()
        response = generate_shreckworld_structured_output(
            usage_tag="architect_ontology_" + stage, system_prompt=prompt,
            user_content=content, messages=messages,
            schema_name="architect_ontology_v6_" + stage,
            response_schema=schema, max_tokens=None,
            model_target=model(run, "reconciliation" if stage == "reconciliation" else "discovery"))
        if row is not None:
            row.input_tokens = (row.input_tokens or 0) + max(0, response.input_tokens or 0)
            row.output_tokens = (row.output_tokens or 0) + max(0, response.output_tokens or 0)
        record_llm_usage(db, run, input_tokens=response.input_tokens,
                         output_tokens=response.output_tokens)
        if not progress(db, run, run.progress, run.stage,
                        {"event": "stage_response_received", "discovery_stage": stage}):
            raise DiscoveryCancelled()
        return response
    return ask


def _window_extract(db, run: OntologyLearnRun, item, doc, batch, content: str,
                    lookup: dict, row: OntologyLearnWindow, stats: dict,
                    known=None, decisions=None) -> list[dict]:
    known = known if known is not None else graph_registry(ontology_studio.graph(db, run.ontology_id))
    # Chapter output depends on the whole prior registry, both model targets,
    # stage contracts AND evidence mapping. Source-only V5 caches are invalid.
    row.fingerprint = hashlib.sha256(compact_json({
        "version": CONTRACT_VERSION, "content": content, "lookup": lookup,
        "registry": registry_payload(known), "models": run.model_targets,
        "system": SYSTEM_PROMPT, "stages": STAGES,
        "duplicate_prompt": DUPLICATE_PROMPT, "duplicate_schema": DUPLICATE_SCHEMA,
    }).encode("utf-8")).hexdigest()
    cached = _reusable_window(db, row)
    if cached is not None:
        row.proposals = cached.proposals
        row.status, row.cached = "completed", True
        row.validation_diagnostics = cached.validation_diagnostics or []
        row.finished_at = datetime.now(timezone.utc)
        db.commit()
        stats.update(proposal_counts(cached.proposals or []))
        stats["cached"] = True
        return cached.proposals or []
    row.status = "running"
    db.commit()

    def checkpoint(proposals, stage):
        row.proposals = proposals
        row.validation_diagnostics = list(stats.get("malformed_parts", []))
        db.commit()
        if not progress(db, run, run.progress, "discovering", {
            "event": "stage_completed", "discovery_stage": stage,
            "chapter_id": row.chapter_id, **proposal_counts(proposals),
        }):
            raise DiscoveryCancelled()

    try:
        proposals = discover_chapter(content, lookup, {
            "source_id": item.id, "document_id": doc.id,
            "content_digest": doc.source_sha256, "chapter_id": batch["chapter_id"],
            "chapter_title": batch["chapter_title"],
        }, known, _ask(db, run, row), checkpoint, stats, decisions)
        row.proposals = proposals
        row.status = "completed"
        row.finished_at = datetime.now(timezone.utc)
        db.commit()
        stats.update(proposal_counts(proposals))
        return proposals
    except Exception as exc:
        db.rollback()
        row.status = "cancelled" if isinstance(exc, DiscoveryCancelled) else "failed"
        row.error_code = exc.code if isinstance(exc, CompactContractError) else (
            "cancelled" if isinstance(exc, DiscoveryCancelled) else "model_request_failed")
        row.validation_diagnostics = list(stats.get("malformed_parts", []))
        row.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise


def _reconcile(db, run: OntologyLearnRun, found: dict, known=None, decisions=None) -> list[dict]:
    known = known if known is not None else graph_registry(ontology_studio.graph(db, run.ontology_id))
    return reconcile(list(found.values()), known, _ask(db, run), decisions)


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
            known = graph_registry(ontology_studio.graph(db, run.ontology_id))
            decisions: dict = {}
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
                                             content, lookup, checkpoint, window_stats,
                                             [*known, *found.values()], decisions)
                for candidate in discovered:
                    accumulate(found, candidate)
                completed += 1
                chapter_key = (checkpoint.source_id, checkpoint.chapter_id)
                remaining_by_chapter[chapter_key] -= 1
                if remaining_by_chapter[chapter_key] == 0:
                    # A chapter is "sent" only after its complete sequential
                    # conversation produced a validated, persisted extraction.
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
                                    "malformed_parts": window_stats.get("malformed_parts", []),
                                    "cached": bool(checkpoint.cached),
                                    "window_input_tokens": checkpoint.input_tokens,
                                    "window_output_tokens": checkpoint.output_tokens,
                                }):
                    return
            proposals = _reconcile(db, run, found, known, decisions)
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
        except DiscoveryCancelled:
            return
        except Exception as exc:
            db.rollback()
            logger.exception("Architect ontology V6 failed for run %s", run_id)
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
