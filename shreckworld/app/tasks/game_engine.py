"""Persist a Phase 1 placeholder run result; extraction is intentionally deferred."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import update

from app.celery_app import celery_app
from app.db.session import session_factory
from app.models.game_engine import GameGenerationRun

logger = logging.getLogger(__name__)


@celery_app.task(name="shreckworld.game_engine.placeholder")
def run_generation_placeholder(run_id: str) -> None:
    session = session_factory()()
    try:
        now = datetime.now(timezone.utc)
        claimed = session.execute(update(GameGenerationRun).where(
            GameGenerationRun.id == run_id, GameGenerationRun.status == "queued",
        ).values(status="running", started_at=now, updated_at=now))
        if claimed.rowcount != 1:
            return
        session.commit()
        run = session.get(GameGenerationRun, run_id)
        if run is None:
            return
        # This durable task deliberately does no extraction or model call.
        session.refresh(run)
        if run.status == "cancel_requested":
            run.status = "cancelled"
            run.result = None
            run.finished_at = run.updated_at = datetime.now(timezone.utc)
            session.commit()
            return
        if run.status != "running":
            return
        run.status = "succeeded"
        run.result = {"execution_mode": "placeholder", "candidates_created": 0,
                      "extraction_performed": False}
        run.diagnostics = [{"code": "extraction_not_implemented", "retryable": False}]
        run.finished_at = run.updated_at = datetime.now(timezone.utc)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("ShreckWorld game-engine placeholder run %s failed", run_id)
        run = session.get(GameGenerationRun, run_id)
        if run is not None:
            run.status, run.error_code = "failed", "placeholder_run_failed"
            run.finished_at = run.updated_at = datetime.now(timezone.utc)
            session.commit()
        raise
    finally:
        session.close()


@celery_app.task(name="shreckworld.game_engine.entity_discovery")
def run_entity_discovery(run_id: str) -> None:
    """Discover provisional game concepts from pinned Book Map generations."""
    import json
    import re
    from collections import OrderedDict
    from sqlalchemy import select
    from app.models.document import (ShreckWorldDocument, ShreckWorldDocumentBookMap,
        ShreckWorldDocumentBlock, ShreckWorldDocumentPage, ShreckWorldDocumentSection)
    from app.models.shreckworld import ShreckWorldLibraryItem
    from app.integrations.shreckllm import generate_shreckworld_structured_output
    from app.schemas.shreckworld import ShreckWorldModelTarget

    session = session_factory()()
    try:
        now = datetime.now(timezone.utc)
        claimed = session.execute(update(GameGenerationRun).where(
            GameGenerationRun.id == run_id, GameGenerationRun.status == "queued",
        ).values(status="running", started_at=now, updated_at=now))
        if claimed.rowcount != 1:
            return
        session.commit()
        run = session.get(GameGenerationRun, run_id)
        if run is None:
            return
        inputs = run.input_sources
        target = ShreckWorldModelTarget(provider="openrouter", name="deepseek/deepseek-v4.1-flash")
        output_schema = {"type": "object", "additionalProperties": False, "required": ["candidates"], "properties": {
            "candidates": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["name", "aliases", "category", "description", "parent_type", "related_concepts", "evidence_block_ids", "confidence", "uncertainty", "questions"],
                "properties": {
                    "name": {"type": "string"}, "aliases": {"type": "array", "items": {"type": "string"}},
                    "category": {"type": "string", "enum": ["entity_type", "specialization", "relationship", "field_resource", "catalog", "procedure"]},
                    "description": {"type": "string"}, "parent_type": {"type": ["string", "null"]},
                    "related_concepts": {"type": "array", "items": {"type": "string"}},
                    "evidence_block_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "uncertainty": {"type": "string"}, "questions": {"type": "array", "items": {"type": "string"}},
                }}}}}
        candidates_by_key = OrderedDict()
        total_sections = 0
        for source in inputs:
            document_id = source["document_id"]
            document = session.get(ShreckWorldDocument, document_id)
            item = session.get(ShreckWorldLibraryItem, source["source_id"])
            map_row = session.get(ShreckWorldDocumentBookMap, document_id)
            if document is None or item is None or not document.is_active or item.ontology_id != run.ontology_id or document.source_sha256 != source["content_digest"] or map_row is None:
                raise ValueError("pinned_source_unavailable")
            block_rows = list(session.scalars(select(ShreckWorldDocumentBlock).where(
                ShreckWorldDocumentBlock.document_id == document_id).order_by(ShreckWorldDocumentBlock.reading_order)))
            pages = {page.id: page.page_number for page in session.scalars(select(ShreckWorldDocumentPage).where(ShreckWorldDocumentPage.document_id == document_id))}
            section_rows = {section.id: section for section in session.scalars(select(ShreckWorldDocumentSection).where(ShreckWorldDocumentSection.document_id == document_id))}
            blocks_by_section = {}
            for block in block_rows:
                blocks_by_section.setdefault(block.section_id, []).append(block)
            sections = map_row.payload.get("sections", [])
            batches = []
            current = []
            current_size = 0
            for section in sections:
                section_id = section.get("id")
                rows = blocks_by_section.get(section_id, [])
                if not rows:
                    continue
                content = {"section_id": section_id, "heading_path": section.get("heading_path", []), "pages": section.get("pages", {}),
                           "blocks": [{"id": block.id, "type": block.block_type, "text": block.display_text[:5000]} for block in rows]}
                size = sum(len(block["text"]) for block in content["blocks"])
                if current and current_size + size > 12000:
                    batches.append(current); current = []; current_size = 0
                current.append(content); current_size += size
                total_sections += 1
            if current:
                batches.append(current)
            for batch in batches:
                session.refresh(run)
                if run.status == "cancel_requested":
                    run.status = "cancelled"; run.result = None
                    run.finished_at = run.updated_at = datetime.now(timezone.utc); session.commit(); return
                allowed_blocks = {block["id"] for section in batch for block in section["blocks"]}
                user_content = json.dumps({"game": {"name": session.get(GameRuleset, run.ruleset_id).name,
                    "edition": session.get(GameRuleset, run.ruleset_id).edition}, "book": {"title": item.title,
                    "edition": source.get("edition")}, "sections": batch}, ensure_ascii=False)
                raw = generate_shreckworld_structured_output(usage_tag="game_ontology_discovery",
                    system_prompt=("You are stage 1 Entity Discovery for a tabletop game ontology. Identify game concepts supported by the supplied rulebook passages. Do not invent facts, exclude relevant concepts because a heading looks like lore or an example, or create complete schemas. Classify each concept as entity_type, specialization, relationship, field_resource, catalog, or procedure. Evidence block IDs must come from the input. Confidence is advisory and uncertainty/questions must remain explicit. Return exactly the required JSON object."),
                    user_content=user_content, schema_name="game_entity_discovery", response_schema=output_schema,
                    max_tokens=5000, model_target=target)
                parsed = json.loads(raw)
                for candidate in parsed["candidates"]:
                    if not candidate["name"].strip() or not set(candidate["evidence_block_ids"]) <= allowed_blocks:
                        raise ValueError("candidate_evidence_invalid")
                    key = (candidate["category"], re.sub(r"\W+", " ", candidate["name"]).casefold().strip())
                    if key not in candidates_by_key:
                        candidates_by_key[key] = {"id": str(uuid4()), **candidate, "evidence": [], "review_status": "pending"}
                    stored = candidates_by_key[key]
                    stored["aliases"] = sorted(set(stored["aliases"] + candidate["aliases"]))
                    stored["related_concepts"] = sorted(set(stored["related_concepts"] + candidate["related_concepts"]))
                    stored["evidence_block_ids"] = sorted(set(stored["evidence_block_ids"] + candidate["evidence_block_ids"]))
                    stored["confidence"] = min(stored["confidence"], candidate["confidence"])
                    stored["uncertainty"] = "; ".join(filter(None, {stored["uncertainty"], candidate["uncertainty"]}))
                    stored["questions"] = sorted(set(stored["questions"] + candidate["questions"]))
                    stored["evidence"].append({"source_id": item.id, "source_title": item.title, "document_id": document_id,
                        "document_revision_id": document_id, "content_digest": document.source_sha256,
                        "block_ids": candidate["evidence_block_ids"]})
                session.refresh(run)
                if run.status == "cancel_requested":
                    run.status = "cancelled"; run.result = None
                    run.finished_at = run.updated_at = datetime.now(timezone.utc); session.commit(); return
            run.result = {"job_type": "entity_discovery", "stage": "entity_discovery", "progress": 90}
            run.updated_at = datetime.now(timezone.utc); session.commit()
        run.result = {"job_type": "entity_discovery", "stage": "awaiting_review", "progress": 100,
            "sections_processed": total_sections, "candidates": list(candidates_by_key.values())}
        run.status = "awaiting_review"
        run.diagnostics = []
        run.finished_at = run.updated_at = datetime.now(timezone.utc)
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("ShreckWorld entity discovery run %s failed", run_id)
        run = session.get(GameGenerationRun, run_id)
        if run is not None:
            run.status, run.error_code = "failed", "entity_discovery_failed"
            run.diagnostics = [{"code": "entity_discovery_failed", "retryable": True}]
            run.finished_at = run.updated_at = datetime.now(timezone.utc)
            session.commit()
        raise
    finally:
        session.close()
