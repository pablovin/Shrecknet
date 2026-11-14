"""Background Architect Learn Ontology. Extracts proposals; never mutates approved ontology."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from sqlalchemy import select, update
from app.celery_app import celery_app
from app.db.session import session_factory
from app.models.ontology_studio import OntologyLearnRun
from app.models.document import (ShreckWorldDocument, ShreckWorldDocumentBookMap,
    ShreckWorldDocumentBlock)
from app.models.shreckworld import ShreckWorldLibraryItem
from app.schemas.shreckworld import ShreckWorldModelTarget
from app.services.book_map_contract import read_book_map
from app.schemas.book_map import BookMapV4
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
Return each discovered concept with exact input evidence_block_ids. Always include
the types referenced by properties and relationships when supported. Allowed types:
text, integer, number, boolean, date, datetime, json, image, url. If the source
is ambiguous or lacks evidence, omit the proposal. Output strictly the schema."""

def model(run: OntologyLearnRun, stage: str) -> ShreckWorldModelTarget:
    target = run.model_targets[stage]
    # Compatible with the ShreckWorld model-target contract and shared config projection.
    return ShreckWorldModelTarget(provider=target["provider"], name=target["name"],
                                 reasoning=target.get("reasoning", False),
                                 reasoning_effort=target.get("reasoning_effort"))

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
                nodes = mapped.nodes if isinstance(mapped, BookMapV4) else mapped.sections
                blocks = {b.id: b for b in db.scalars(select(ShreckWorldDocumentBlock).where(
                    ShreckWorldDocumentBlock.document_id == doc.id))}
                seen = set()
                batches = []
                current, size = [], 0
                for section in nodes:
                    values = []
                    for block_id in section.source_block_ids:
                        if block_id in seen or block_id not in blocks:
                            continue
                        seen.add(block_id)
                        block = blocks[block_id]
                        values.append({"id": block_id, "text": block.display_text[:5000],
                                       "type": block.block_type})
                    if not values:
                        continue
                    group = {"heading_path": section.heading_path, "blocks": values}
                    length = sum(len(x["text"]) for x in values)
                    if current and size + length > 12000:
                        batches.append(current)
                        current, size = [], 0
                    current.append(group)
                    size += length
                if current:
                    batches.append(current)
                for bi, batch in enumerate(batches):
                    if not progress(db, run, 5 + int(65 * (si + bi / max(1, len(batches))) / total),
                                    "discovering"):
                        return
                    allowed = {b["id"] for group in batch for b in group["blocks"]}
                    raw = generate_shreckworld_structured_output(
                        usage_tag="architect_ontology_discovery", system_prompt=PROMPT,
                        user_content=json.dumps({"source_title": item.title, "sections": batch},
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
                        evidence = {"source_id": item.id, "document_id": doc.id,
                            "content_digest": doc.source_sha256, "block_ids": evidence_ids}
                        proposal = normalize(candidate, evidence)
                        key = proposal_key(proposal)
                        if key in found:
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
                p["id"] = str(__import__("uuid").uuid4())
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
