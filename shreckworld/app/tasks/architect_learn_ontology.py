"""Background Architect Learn Ontology. Extracts proposals; never mutates approved ontology."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select, update
from app.celery_app import celery_app
from app.db.session import session_factory
from app.models.ontology_studio import OntologyLearnRun, OntologyLearnChapter
from app.models.document import (ShreckWorldDocument, ShreckWorldDocumentBookMap,
    ShreckWorldDocumentBlock)
from app.models.shreckworld import ShreckWorldLibraryItem
from app.schemas.shreckworld import ShreckWorldModelTarget
from app.services.book_map_contract import read_book_map
from app.services.ontology_discovery_batches import build_discovery_batches
from app.integrations.shreckllm import generate_shreckworld_structured_output
from app.services import ontology_studio
from app.services.ontology_run_reporting import progress, record_llm_usage

logger = logging.getLogger(__name__)
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["proposals"],
    "properties": {"proposals": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["kind", "name", "entity", "source", "target", "data_type",
                     "description", "evidence_block_ids", "confidence"],
        "properties": {
            "kind": {"type": "string", "enum": ["entity", "property", "relationship"]},
            "name": {"type": "string"},
            "entity": {"type": "string"},
            "source": {"type": "string"},
            "target": {"type": "string"},
            "data_type": {"type": "string"},
            "description": {"type": "string"},
            "evidence_block_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1}
        }}}}
}

PROMPT = """You are the Architect's ontology discovery engine. Learn schema definitions
from an RPG reference source. Discover entity TYPES, typed PROPERTIES belonging to
entity types, and directed RELATIONSHIPS between entity types. Distinguish instances
(e.g. a named weapon or specific skill) from types. Do not invent unsupported fields.
Extract the complete chapter passage into one combined entity/field/relationship schema.\nReturn each discovered concept with exact input evidence_block_ids. Always include
the types referenced by properties and relationships when supported. Allowed types:
text, integer, number, boolean, date, datetime, json, image, url. If the source
is ambiguous or lacks evidence, omit the proposal. Output strictly the schema."""

def model(run: OntologyLearnRun, stage: str) -> ShreckWorldModelTarget:
    target = run.model_targets[stage]
    # Compatible with the ShreckWorld model-target contract and shared config projection.
    return ShreckWorldModelTarget.model_validate(target)

def normalize(item: dict, evidence: dict) -> dict:
    kind = item["kind"]
    out = {"kind": kind, "name": item["name"].strip(),
           "description": item["description"].strip(),
           "evidence": [evidence], "confidence": item["confidence"]}
    if kind == "property":
        out.update(entity=item["entity"].strip(), data_type=item["data_type"].strip().lower())
    if kind == "relationship":
        out.update(source=item["source"].strip(), target=item["target"].strip())
    return out

def proposal_key(p: dict) -> tuple:
    return (p["kind"], p["name"].casefold(), p.get("entity", "").casefold(),
            p.get("source", "").casefold(), p.get("target", "").casefold())

@celery_app.task(name="shreckworld.architect.learn_ontology")
def run_ontology_discovery(run_id: str) -> None:
    with session_factory()() as db:
        claimed = db.execute(update(OntologyLearnRun).where(
            OntologyLearnRun.id == run_id, OntologyLearnRun.status == "queued"
        ).values(status="running", stage="preparing", progress=1))
        if claimed.rowcount != 1:
            db.rollback()
            return
        db.commit()
        try:
            run = db.get(OntologyLearnRun, run_id)
            found = {}
            total = len(run.input_sources)
            prepared = []
            # Preflight every pinned source to compute exact work units before
            # starting LLM calls. No text is duplicated in model requests.
            for source in run.input_sources:
                if not progress(db, run, 1, "preparing"):
                    return
                doc = db.get(ShreckWorldDocument, source["document_id"])
                item = db.get(ShreckWorldLibraryItem, source["source_id"])
                book = db.get(ShreckWorldDocumentBookMap, source["document_id"])
                if (not doc or not item or not book or not doc.is_active or
                    item.source_type != "explanatory" or item.ontology_id != run.ontology_id or
                    doc.library_item_id != item.id or doc.source_sha256 != source["content_digest"]):
                    raise ValueError("pinned_source_unavailable")
                mapped = read_book_map(book.payload)
                blocks = {b.id: b for b in db.scalars(select(ShreckWorldDocumentBlock).where(
                    ShreckWorldDocumentBlock.document_id == doc.id))}
                batches = build_discovery_batches(mapped, blocks)
                selected_chapters = source.get("chapter_ids")
                if selected_chapters is not None:
                    if not set(selected_chapters) <= {batch["chapter_id"] for batch in batches}:
                        raise ValueError("pinned_chapter_unavailable")
                    batches = [batch for batch in batches if batch["chapter_id"] in set(selected_chapters)]
                prepared.append((item, doc, batches))
            total_windows = sum(len(batches) for _, _, batches in prepared)
            completed_windows = 0
            if not progress(db, run, 5, "preparing", {
                "event": "plan_ready", "total_sources": total, "total_windows": total_windows,
                "completed_windows": 0, "proposals_discovered": 0,
            }):
                return
            for si, (item, doc, batches) in enumerate(prepared):
                for bi, batch in enumerate(batches):
                    detail = {
                        "source_id": item.id, "source_title": item.title,
                        "source_index": si + 1, "total_sources": total,
                        "chapter_id": batch["chapter_id"],
                        "chapter_title": batch["chapter_title"],
                        "window_index": bi + 1, "windows_in_source": len(batches),
                        "completed_windows": completed_windows,
                        "total_windows": total_windows,
                        "proposals_discovered": len(found),
                    }
                    percent = 5 + int(65 * completed_windows / max(1, total_windows))
                    if not progress(db, run, percent, "discovering",
                                    {"event": "window_started", **detail}):
                        return
                    allowed = {b["id"] for group in batch["sections"] for b in group["blocks"]}
                    block_sections = {b["id"]: group for group in batch["sections"] for b in group["blocks"]}
                    response = generate_shreckworld_structured_output(
                        usage_tag="architect_ontology_discovery", system_prompt=PROMPT,
                        user_content=json.dumps({"source_title": item.title, **batch},
                                                ensure_ascii=False),
                        schema_name="architect_ontology_discovery", response_schema=SCHEMA,
                        max_tokens=6000, model_target=model(run, "discovery"))
                    sent = db.scalar(select(OntologyLearnChapter).where(
                        OntologyLearnChapter.ontology_id == run.ontology_id,
                        OntologyLearnChapter.source_id == item.id,
                        OntologyLearnChapter.content_digest == doc.source_sha256,
                        OntologyLearnChapter.chapter_id == batch["chapter_id"]))
                    if sent is None:
                        sent = OntologyLearnChapter(ontology_id=run.ontology_id,
                            source_id=item.id, document_id=doc.id, content_digest=doc.source_sha256,
                            chapter_id=batch["chapter_id"], chapter_title=batch["chapter_title"])
                        db.add(sent)
                    else:
                        sent.document_id = doc.id
                        sent.chapter_title = batch["chapter_title"]
                        sent.last_sent_at = datetime.now(timezone.utc)
                    db.commit()
                    record_llm_usage(db, run, input_tokens=response.input_tokens,
                                     output_tokens=response.output_tokens)
                    result = json.loads(response.text)
                    for candidate in result["proposals"]:
                        evidence_ids = candidate["evidence_block_ids"]
                        if not candidate["name"].strip() or not set(evidence_ids) <= allowed:
                            raise ValueError("invalid_ontology_evidence")
                        if candidate["kind"] == "property" and (not candidate["entity"].strip() or
                             candidate["data_type"].strip() not in ontology_studio.TYPES):
                            continue
                        if candidate["kind"] == "relationship" and (
                            not candidate["source"].strip() or not candidate["target"].strip()):
                            continue
                        section_ids = list(dict.fromkeys(
                            block_sections[block_id]["section_id"] for block_id in evidence_ids
                        ))
                        evidence = {"source_id": item.id, "document_id": doc.id,
                            "content_digest": doc.source_sha256, "block_ids": evidence_ids,
                            "chapter_id": batch["chapter_id"],
                            "chapter_title": batch["chapter_title"],
                            "section_ids": section_ids}
                        proposal = normalize(candidate, evidence)
                        key = proposal_key(proposal)
                        if key in found:
                            if (candidate["kind"] == "property" and
                                found[key].get("data_type") != proposal.get("data_type")):
                                found[key]["conflict"] = {
                                    "reason": "inconsistent_source_property_type",
                                    "alternatives": sorted({found[key]["data_type"], proposal["data_type"]})
                                }
                            if evidence not in found[key]["evidence"]:
                                found[key]["evidence"].append(evidence)
                        else:
                            found[key] = proposal
                    completed_windows += 1
                    if not progress(db, run,
                                    5 + int(65 * completed_windows / max(1, total_windows)),
                                    "discovering", {"event": "window_completed",
                                    **detail, "completed_windows": completed_windows,
                                    "proposals_discovered": len(found)}):
                        return
            if not progress(db, run, 75, "reconciling",
                            {"event": "reconciliation_started",
                             "completed_windows": completed_windows,
                             "total_windows": total_windows,
                             "proposals_discovered": len(found)}):
                return
                return
            # Reconciliation must never replace approved edits or rewrite the graph.
            current = ontology_studio.graph(db, run.ontology_id)
            entities = {e["name"].casefold() for e in current["entities"]}
            properties = {(e["name"].casefold(), p["name"].casefold())
                          for e in current["entities"] for p in current["properties"]
                          if p["entity_id"] == e["id"]}
            relations = {(r["source_id"], r["target_id"], r["name"].casefold())
                         for r in current["relationships"]}
            names = {e["id"]: e["name"].casefold() for e in current["entities"]}
            # Independently configurable reconciliation model proposes additional
            # source-supported schema changes, but never edits the canonical graph.
            if found:
                grouped = list(found.values())
                total_groups = (len(grouped) + 49) // 50
                for offset in range(0, len(grouped), 50):
                    group = grouped[offset:offset + 50]
                    group_index = offset // 50 + 1
                    if not progress(db, run, 75 + int(19 * (group_index - 1) / total_groups),
                                    "reconciling", {
                                    "event": "reconciliation_group_started",
                                    "group_index": group_index, "total_groups": total_groups,
                                    "proposals_discovered": len(found)}):
                        return
                    reconciliation_response = generate_shreckworld_structured_output(
                        usage_tag="architect_ontology_reconciliation",
                        system_prompt=(PROMPT + "\\nReconcile terminology in the candidate list "
                                       "against existing ontology definitions. Return only justified "
                                       "candidate objects, preserving provided evidence IDs."),
                        user_content=json.dumps({"candidates": group,
                            "existing_properties": current["properties"][:100],
                            "existing_entities": current["entities"][:100]}, ensure_ascii=False),
                        schema_name="architect_ontology_reconciliation", response_schema=SCHEMA,
                        max_tokens=6000, model_target=model(run, "reconciliation"))
                    record_llm_usage(db, run, input_tokens=reconciliation_response.input_tokens,
                                     output_tokens=reconciliation_response.output_tokens)
                    reconciled = json.loads(reconciliation_response.text)
                    permitted = {block for proposal in group for ev in proposal["evidence"]
                                 for block in ev.get("block_ids", [])}
                    evidence_lookup = {block: ev for proposal in group for ev in proposal["evidence"]
                                       for block in ev.get("block_ids", [])}
                    for suggestion in reconciled["proposals"]:
                        if (not suggestion["name"].strip() or
                            not set(suggestion["evidence_block_ids"]) <= permitted):
                            continue
                        if suggestion["kind"] == "property" and suggestion["data_type"] not in ontology_studio.TYPES:
                            continue
                        if suggestion["kind"] == "relationship" and (
                            not suggestion["source"].strip() or not suggestion["target"].strip()):
                            continue
                        evidence = [evidence_lookup[b] for b in suggestion["evidence_block_ids"]]
                        candidate = normalize(suggestion, evidence[0])
                        candidate["evidence"] = list({json.dumps(x, sort_keys=True): x for x in evidence}.values())
                        key = proposal_key(candidate)
                        if key in found:
                            old = found[key]
                            old["evidence"] = list({json.dumps(x, sort_keys=True): x
                                                    for x in (old["evidence"] + candidate["evidence"])}.values())
                        else:
                            found[key] = candidate
                    if not progress(db, run, 75 + int(19 * group_index / total_groups),
                                    "reconciling", {
                                    "event": "reconciliation_group_completed",
                                    "group_index": group_index, "total_groups": total_groups,
                                    "proposals_discovered": len(found)}):
                        return
            proposals = []
            for p in found.values():
                kind = p["kind"]
                if kind == "entity":
                    p["change"] = "update" if p["name"].casefold() in entities else "new"
                elif kind == "property":
                    p["change"] = ("update" if (p["entity"].casefold(), p["name"].casefold()) in properties
                                   else "new")
                else:
                    p["change"] = ("update" if (p["source"].casefold(), p["target"].casefold(),
                                    p["name"].casefold()) in {(names.get(s),names.get(t),n) for s,t,n in relations}
                                   else "new")
                if p.get("conflict"):
                    p["change"] = "conflict"
                p["id"] = str(uuid4())
                proposals.append(p)
            if not progress(db, run, 95, "persisting", {
                    "event": "proposals_ready", "proposals_discovered": len(proposals),
                    "completed_windows": completed_windows, "total_windows": total_windows}):
                return
            # Refresh once more so cancellation remains cooperative even when
            # requested during proposal finalization.
            db.refresh(run)
            if run.status == "cancel_requested":
                progress(db, run, run.progress, "persisting")
                return
            run.proposals = proposals
            run.progress = 100
            run.stage = "awaiting_review"
            run.status = "awaiting_review"
            run.diagnostics = [*(run.diagnostics or []), {
                "kind": "progress", "event": "awaiting_review",
                "stage": "awaiting_review", "progress": 100,
                "proposals_discovered": len(proposals),
                "completed_windows": completed_windows, "total_windows": total_windows,
                "at": datetime.now(timezone.utc).isoformat()}][-250:]
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.exception("Architect ontology learning failed for run %s", run_id)
            run = db.get(OntologyLearnRun, run_id)
            if run is not None:
                run.status = "failed"
                run.stage = "failed"
                run.diagnostics = [*(run.diagnostics or []), {
                    "kind": "error", "code": "ontology_discovery_failed",
                    "failure_type": type(exc).__name__,
                    "at": datetime.now(timezone.utc).isoformat()}][-250:]
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
            raise
