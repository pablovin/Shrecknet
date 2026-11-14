"""Background Architect Learn Ontology. Extracts proposals; never mutates approved ontology."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select, update
from app.celery_app import celery_app
from app.db.session import session_factory
from app.models.ontology_studio import OntologyLearnRun
from app.models.document import (ShreckWorldDocument, ShreckWorldDocumentBookMap,
    ShreckWorldDocumentBlock)
from app.models.shreckworld import ShreckWorldLibraryItem
from app.schemas.shreckworld import ShreckWorldModelTarget
from app.services.book_map_contract import read_book_map
from app.services.ontology_discovery_batches import build_discovery_batches
from app.integrations.shreckllm import generate_shreckworld_structured_output
from app.services import ontology_studio

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

def progress(db, run: OntologyLearnRun, percent: int, stage: str) -> bool:
    db.refresh(run)
    if run.status == "cancel_requested":
        run.status, run.stage = "cancelled", "cancelled"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return False
    run.progress, run.stage = percent, stage
    db.commit()
    return True

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
            for si, source in enumerate(run.input_sources):
                if not progress(db, run, 5 + int(65 * si / total), "discovering"):
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
                # One combined entity+schema extraction per bounded chapter window.
                # Chapters provide semantic context; subsections preserve provenance.
                batches = build_discovery_batches(mapped, blocks)
                for bi, batch in enumerate(batches):
                    if not progress(db, run, 5 + int(65 * (si + bi / max(1, len(batches))) / total),
                                    "discovering"):
                        return
                    allowed = {b["id"] for group in batch["sections"] for b in group["blocks"]}
                    block_sections = {b["id"]: group for group in batch["sections"] for b in group["blocks"]}
                    raw = generate_shreckworld_structured_output(
                        usage_tag="architect_ontology_discovery", system_prompt=PROMPT,
                        user_content=json.dumps({"source_title": item.title, **batch},
                                                ensure_ascii=False),
                        schema_name="architect_ontology_discovery", response_schema=SCHEMA,
                        max_tokens=6000, model_target=model(run, "discovery"))
                    result = json.loads(raw)
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
            if not progress(db, run, 75, "reconciling"):
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
                for offset in range(0, len(grouped), 50):
                    group = grouped[offset:offset + 50]
                    reconciliation_raw = generate_shreckworld_structured_output(
                        usage_tag="architect_ontology_reconciliation",
                        system_prompt=(PROMPT + "\\nReconcile terminology in the candidate list "
                                       "against existing ontology definitions. Return only justified "
                                       "candidate objects, preserving provided evidence IDs."),
                        user_content=json.dumps({"candidates": group,
                            "existing_properties": current["properties"][:100],
                            "existing_entities": current["entities"][:100]}, ensure_ascii=False),
                        schema_name="architect_ontology_reconciliation", response_schema=SCHEMA,
                        max_tokens=6000, model_target=model(run, "reconciliation"))
                    reconciled = json.loads(reconciliation_raw)
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
            if not progress(db, run, 95, "persisting"):
                return
            run.proposals = proposals
            run.progress = 100
            run.stage = "awaiting_review"
            run.status = "awaiting_review"
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.exception("Architect ontology learning failed for run %s", run_id)
            run = db.get(OntologyLearnRun, run_id)
            if run is not None:
                run.status = "failed"
                run.stage = "failed"
                run.diagnostics = [{"code": "ontology_discovery_failed",
                                    "failure_type": type(exc).__name__}]
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
            raise
