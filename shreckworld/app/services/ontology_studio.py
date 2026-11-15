"""Ontology Studio domain operations. All calls must be scoped by authorized ontology_id."""
from __future__ import annotations
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from app.models.ontology_studio import (WorldOntology, OntologyEntity, OntologyProperty,
    OntologyRelationship, OntologyRevision, OntologyLearnRun, OntologyLearnSource,
    OntologyLearnWindow)

TYPES = {"text", "integer", "number", "boolean", "date", "datetime", "json", "image", "url"}
CARDINALITY = {"one", "many"}


def validate_property_spec(data: dict) -> None:
    """Validate world-local semantic metadata and instance value fields."""
    from fastapi import HTTPException
    data_type = data.get("data_type")
    if data_type not in TYPES:
        raise HTTPException(422, "Invalid property data_type")
    semantic = data.get("semantic_type")
    if semantic is not None and (not isinstance(semantic, str) or len(semantic) > 100):
        raise HTTPException(422, "Invalid semantic_type")
    for field in ("definition", "value_schema"):
        if not isinstance(data.get(field) or {}, dict):
            raise HTTPException(422, "Invalid " + field)
    spec = data.get("value_schema") or {}
    current = spec.get("current_value")
    if current is None:
        return  # Legacy definition; new stateful definitions add the slot below.
    if (not isinstance(current, dict) or current.get("type") != data_type or
        current.get("required") is not True):
        raise HTTPException(422, "current_value must be required and match property data_type")
    allowed = TYPES | {"array", "object", "reference", "enum"}
    for field, item in spec.items():
        if (not isinstance(field, str) or not field or not isinstance(item, dict)
            or item.get("type") not in allowed or
            not isinstance(item.get("required", False), bool)
            or not isinstance(item.get("nullable", True), bool)):
            raise HTTPException(422, "Invalid instance field schema")


def normalized_property_data(data: dict) -> dict:
    """Default a value slot for all new properties while keeping legacy reads safe."""
    result = dict(data)
    validate_property_spec(result)
    spec = dict(result.get("value_schema") or {})
    spec.setdefault("current_value", {"type": result["data_type"],
                                       "required": True, "nullable": True})
    result["value_schema"] = spec
    result.setdefault("definition", {})
    return result


def instantiate_entity_properties(graph_data: dict, entity_id: str) -> dict:
    """Materialize the world-defined property fields, without writing a graph instance."""
    entities = {e["id"]: e for e in graph_data["entities"]}
    if entity_id not in entities:
        raise HTTPException(404, "Entity type not found")
    parents = []
    seen = set()
    current = entity_id
    while current:
        if current in seen or current not in entities:
            raise HTTPException(422, "Invalid entity inheritance")
        seen.add(current)
        parents.insert(0, current)
        current = entities[current].get("parent_id")
    fields = {}
    for p in graph_data["properties"]:
        if p["entity_id"] not in parents:
            continue
        spec = p.get("value_schema") or {"current_value": {
            "type": p["data_type"], "required": True, "nullable": True}}
        fields[p["name"]] = {name: None for name in spec}
    return fields



def delete_run(db: Session, run: OntologyLearnRun) -> dict[str, int]:
    """Delete a terminal run and invalidate every reusable window it established."""
    from app.models.ontology_studio import OntologyLearnChapter

    if run.status in {"queued", "running", "cancel_requested"}:
        raise HTTPException(409, "Active runs cannot be deleted")

    windows = list(db.scalars(select(OntologyLearnWindow).where(
        OntologyLearnWindow.run_id == run.id,
        OntologyLearnWindow.ontology_id == run.ontology_id,
    )))
    chapter_keys = {
        (row.source_id, row.document_id, row.content_digest, row.chapter_id)
        for row in windows
    }

    active_runs = list(db.scalars(select(OntologyLearnRun).where(
        OntologyLearnRun.ontology_id == run.ontology_id,
        OntologyLearnRun.id != run.id,
        OntologyLearnRun.status.in_({"queued", "running", "cancel_requested"}),
    )))
    for source_id, _document_id, digest, chapter_id in chapter_keys:
        for active in active_runs:
            for source in active.input_sources or []:
                if source.get("source_id") != source_id or source.get("content_digest") != digest:
                    continue
                selected = source.get("chapter_ids")
                if selected is None or chapter_id in selected:
                    raise HTTPException(409, "Another active run is processing one of this run's chapters")

    # Delete every checkpoint for these chapters and every matching fingerprint
    # copy, since cache lookup is content-based and can cross chapter boundaries.
    removed_checkpoints = 0
    for source_id, _document_id, digest, chapter_id in chapter_keys:
        fingerprints = {row.fingerprint for row in windows
                        if row.source_id == source_id and row.content_digest == digest
                        and row.chapter_id == chapter_id}
        removed_checkpoints += db.query(OntologyLearnWindow).filter(
            OntologyLearnWindow.ontology_id == run.ontology_id,
            OntologyLearnWindow.source_id == source_id,
            OntologyLearnWindow.content_digest == digest,
            or_(OntologyLearnWindow.chapter_id == chapter_id,
                OntologyLearnWindow.fingerprint.in_(fingerprints)),
        ).delete(synchronize_session=False)

    for source_id, _document_id, digest, chapter_id in chapter_keys:
        db.query(OntologyLearnChapter).filter(
            OntologyLearnChapter.ontology_id == run.ontology_id,
            OntologyLearnChapter.source_id == source_id,
            OntologyLearnChapter.content_digest == digest,
            OntologyLearnChapter.chapter_id == chapter_id,
        ).delete(synchronize_session=False)
    db.query(OntologyLearnSource).filter(OntologyLearnSource.run_id == run.id).delete(
        synchronize_session=False)
    db.delete(run)
    db.commit()
    return {"deleted_runs": 1, "cleared_checkpoints": removed_checkpoints,
            "cleared_chapters": len(chapter_keys)}


def restore_review(db: Session, run: OntologyLearnRun, author: str) -> OntologyLearnRun:
    """Undo this run's latest ontology revision and return its proposals to review."""
    if run.status != "succeeded" or not run.proposals:
        raise HTTPException(409, "Only a reviewed run with proposals can be reopened")
    owner = root(db, run.ontology_id)
    if owner is None:
        raise HTTPException(409, "The ontology no longer exists")
    applied_revision = run.applied_revision or run.base_revision + 1
    if owner.revision != applied_revision:
        raise HTTPException(409, "The ontology changed after this run; its approved changes cannot be safely undone")
    if run.base_revision == 0:
        previous = {"entities": [], "properties": [], "relationships": []}
    else:
        previous = db.scalar(select(OntologyRevision.snapshot).where(
            OntologyRevision.root_id == owner.id,
            OntologyRevision.revision == run.base_revision,
        ))
        if previous is None:
            raise HTTPException(409, "The original ontology revision is unavailable")

    entity_ids = list(db.scalars(select(OntologyEntity.id).where(OntologyEntity.root_id == owner.id)))
    if entity_ids:
        db.query(OntologyEntity).filter(OntologyEntity.root_id == owner.id).update(
            {"parent_id": None}, synchronize_session=False)
        db.query(OntologyProperty).filter(OntologyProperty.entity_id.in_(entity_ids)).delete(
            synchronize_session=False)
    db.query(OntologyRelationship).filter(OntologyRelationship.root_id == owner.id).delete(
        synchronize_session=False)
    db.query(OntologyEntity).filter(OntologyEntity.root_id == owner.id).delete(
        synchronize_session=False)
    db.flush()
    for entity in previous.get("entities", []):
        db.add(OntologyEntity(
            id=entity["id"], root_id=owner.id, name=entity["name"],
            description=entity.get("description", ""), aliases=entity.get("aliases", []),
            evidence=entity.get("evidence", []), review_status=entity.get("review_status", "approved"),
        ))
    db.flush()
    for entity in previous.get("entities", []):
        if entity.get("parent_id"):
            db.get(OntologyEntity, entity["id"]).parent_id = entity["parent_id"]
    for prop in previous.get("properties", []):
        db.add(OntologyProperty(
            id=prop["id"], entity_id=prop["entity_id"], name=prop["name"],
            data_type=prop["data_type"], required=prop.get("required", False),
            cardinality=prop.get("cardinality", "one"), description=prop.get("description", ""),
            constraints=prop.get("constraints", {}),
            semantic_type=prop.get("semantic_type"),
            definition=prop.get("definition", {}),
            value_schema=prop.get("value_schema", {}),
            evidence=prop.get("evidence", []),
        ))
    for relation in previous.get("relationships", []):
        db.add(OntologyRelationship(
            id=relation["id"], root_id=owner.id, source_id=relation["source_id"],
            target_id=relation["target_id"], name=relation["name"],
            description=relation.get("description", ""), cardinality=relation.get("cardinality", "many"),
            inverse_name=relation.get("inverse_name"), evidence=relation.get("evidence", []),
        ))
    snapshot(db, owner, author)
    run.proposals = [{key: value for key, value in proposal.items() if key != "decision"}
                     for proposal in run.proposals]
    run.base_revision = owner.revision
    run.applied_revision = None
    run.proposal_revision += 1
    run.status, run.stage, run.progress = "awaiting_review", "awaiting_review", 100
    run.finished_at = None
    db.commit()
    return run


def recover_empty_review(db: Session, run: OntologyLearnRun) -> OntologyLearnRun:
    """Restore a lost empty review from persisted, validated window proposals."""
    from uuid import uuid4
    from app.services.ontology_compact import accumulate

    if run.status != "succeeded" or run.proposals:
        raise HTTPException(409, "This run does not have an empty completed review")
    owner = root(db, run.ontology_id)
    if owner is None or owner.revision != run.base_revision + 1:
        raise HTTPException(409, "The ontology changed after this run; proposals cannot be safely restored")
    found: dict = {}
    windows = db.scalars(select(OntologyLearnWindow).where(
        OntologyLearnWindow.run_id == run.id,
        OntologyLearnWindow.status == "completed",
    ).order_by(OntologyLearnWindow.source_id, OntologyLearnWindow.chapter_id,
               OntologyLearnWindow.window_index))
    for window in windows:
        for proposal in window.proposals or []:
            accumulate(found, proposal)
    if not found:
        raise HTTPException(409, "No saved window proposals are available to restore")
    run.proposals = [{**proposal, "id": str(uuid4())} for proposal in found.values()]
    run.base_revision = owner.revision
    run.applied_revision = None
    run.proposal_revision += 1
    run.status, run.stage, run.progress = "awaiting_review", "awaiting_review", 100
    run.finished_at = None
    run.diagnostics = [*(run.diagnostics or []), {
        "kind": "warning", "code": "recovered_unreconciled_window_proposals",
        "at": datetime.now(timezone.utc).isoformat(),
    }][-250:]
    db.commit()
    return run

def root(session: Session, ontology_id: int) -> WorldOntology | None:
    return session.scalar(select(WorldOntology).where(WorldOntology.ontology_id == ontology_id))

def ensure_root(session: Session, ontology_id: int) -> WorldOntology:
    row = root(session, ontology_id)
    if row is None:
        row = WorldOntology(ontology_id=ontology_id)
        session.add(row)
        session.flush()
    return row

def graph(session: Session, ontology_id: int) -> dict:
    owner = root(session, ontology_id)
    if owner is None:
        return {"revision": 0, "entities": [], "properties": [], "relationships": []}
    entities = list(session.scalars(select(OntologyEntity).where(
        OntologyEntity.root_id == owner.id, OntologyEntity.deleted.is_(False)).order_by(OntologyEntity.name)))
    ids = [e.id for e in entities]
    props = list(session.scalars(select(OntologyProperty).where(OntologyProperty.entity_id.in_(ids)))) if ids else []
    rels = list(session.scalars(select(OntologyRelationship).where(OntologyRelationship.root_id == owner.id)))
    entity_ids = set(ids)
    return {"revision": owner.revision,
        "entities": [{"id": e.id, "name": e.name, "description": e.description, "aliases": e.aliases,
                      "parent_id": e.parent_id, "evidence": e.evidence, "review_status": e.review_status} for e in entities],
        "properties": [{"id": p.id, "entity_id": p.entity_id, "name": p.name, "data_type": p.data_type,
                        "required": p.required, "cardinality": p.cardinality, "description": p.description,
                        "constraints": p.constraints, "semantic_type": p.semantic_type,
                        "definition": p.definition or {}, "value_schema": p.value_schema or {},
                        "evidence": p.evidence} for p in props],
        "relationships": [{"id": r.id, "source_id": r.source_id, "target_id": r.target_id,
                           "name": r.name, "description": r.description, "cardinality": r.cardinality,
                           "inverse_name": r.inverse_name, "evidence": r.evidence} for r in rels
                          if r.source_id in entity_ids and r.target_id in entity_ids]}

def revision_check(owner: WorldOntology, expected_revision: int) -> None:
    if owner.revision != expected_revision:
        raise HTTPException(409, "Ontology revision conflict; reload before editing")

def snapshot(session: Session, owner: WorldOntology, author: str) -> None:
    owner.revision += 1
    session.flush()
    session.add(OntologyRevision(root_id=owner.id, revision=owner.revision, author_id=author,
                                 snapshot=graph(session, owner.ontology_id)))

def resolve_entity(session: Session, owner: WorldOntology, entity_id: str) -> OntologyEntity:
    row = session.get(OntologyEntity, entity_id)
    if row is None or row.root_id != owner.id or row.deleted:
        raise HTTPException(404, "Entity not found")
    return row

def validate_parent(session: Session, owner: WorldOntology, entity_id: str | None, parent_id: str | None):
    if not parent_id:
        return
    if parent_id == entity_id:
        raise HTTPException(422, "Entity cannot inherit from itself")
    cur = resolve_entity(session, owner, parent_id)
    seen = {entity_id}
    while cur is not None:
        if cur.id in seen:
            raise HTTPException(422, "Inheritance cycle")
        seen.add(cur.id)
        cur = resolve_entity(session, owner, cur.parent_id) if cur.parent_id else None

def create_entity(session: Session, ontology_id: int, data: dict, expected_revision: int, author: str) -> dict:
    owner = ensure_root(session, ontology_id)
    revision_check(owner, expected_revision)
    validate_parent(session, owner, None, data.get("parent_id"))
    name = data["name"].strip()
    if not name:
        raise HTTPException(422, "Entity name is required")
    if session.scalar(select(OntologyEntity.id).where(OntologyEntity.root_id == owner.id,
                      OntologyEntity.name == name)):
        raise HTTPException(409, "Entity name already exists")
    row = OntologyEntity(root_id=owner.id, name=name, description=data.get("description", ""),
                         aliases=data.get("aliases", []), parent_id=data.get("parent_id"))
    session.add(row)
    snapshot(session, owner, author)
    session.commit()
    return graph(session, ontology_id)

def update_entity(session: Session, ontology_id: int, entity_id: str, data: dict,
                  expected_revision: int, author: str) -> dict:
    owner = ensure_root(session, ontology_id)
    revision_check(owner, expected_revision)
    row = resolve_entity(session, owner, entity_id)
    if "parent_id" in data:
        validate_parent(session, owner, entity_id, data["parent_id"])
    for field in ("name", "description", "aliases", "parent_id"):
        if field in data:
            setattr(row, field, data[field])
    if not isinstance(row.name, str) or not row.name.strip():
        raise HTTPException(422, "Entity name is required")
    snapshot(session, owner, author)
    session.commit()
    return graph(session, ontology_id)

def delete_entity(session: Session, ontology_id: int, entity_id: str,
                  expected_revision: int, author: str) -> dict:
    owner = ensure_root(session, ontology_id)
    revision_check(owner, expected_revision)
    row = resolve_entity(session, owner, entity_id)
    session.query(OntologyRelationship).filter(
        OntologyRelationship.root_id == owner.id,
        ((OntologyRelationship.source_id == entity_id) | (OntologyRelationship.target_id == entity_id))).delete(synchronize_session=False)
    session.query(OntologyEntity).filter(OntologyEntity.root_id == owner.id,
        OntologyEntity.parent_id == entity_id).update({"parent_id": None}, synchronize_session=False)
    row.deleted = True
    snapshot(session, owner, author)
    session.commit()
    return graph(session, ontology_id)

def create_property(session: Session, ontology_id: int, entity_id: str, data: dict,
                    expected_revision: int, author: str) -> dict:
    owner = ensure_root(session, ontology_id)
    revision_check(owner, expected_revision)
    resolve_entity(session, owner, entity_id)
    if data["data_type"] not in TYPES or data.get("cardinality", "one") not in CARDINALITY:
        raise HTTPException(422, "Invalid property type or cardinality")
    p = OntologyProperty(entity_id=entity_id, **normalized_property_data(data))
    session.add(p)
    snapshot(session, owner, author)
    session.commit()
    return graph(session, ontology_id)

def update_property(session: Session, ontology_id: int, property_id: str, data: dict,
                    expected_revision: int, author: str) -> dict:
    owner = ensure_root(session, ontology_id)
    revision_check(owner, expected_revision)
    p = session.get(OntologyProperty, property_id)
    if p is None:
        raise HTTPException(404, "Property not found")
    resolve_entity(session, owner, p.entity_id)
    updated = {**{"data_type": p.data_type, "semantic_type": p.semantic_type,
                  "definition": p.definition, "value_schema": p.value_schema}, **data}
    validate_property_spec(updated)
    for k,v in data.items():
        setattr(p,k,v)
    if p.data_type not in TYPES or p.cardinality not in CARDINALITY:
        raise HTTPException(422, "Invalid property type or cardinality")
    snapshot(session, owner, author)
    session.commit()
    return graph(session, ontology_id)

def delete_property(session: Session, ontology_id: int, property_id: str,
                    expected_revision: int, author: str) -> dict:
    owner = ensure_root(session, ontology_id)
    revision_check(owner, expected_revision)
    p = session.get(OntologyProperty, property_id)
    if p is None:
        raise HTTPException(404, "Property not found")
    resolve_entity(session, owner, p.entity_id)
    session.delete(p)
    snapshot(session, owner, author)
    session.commit()
    return graph(session, ontology_id)

def create_relationship(session: Session, ontology_id: int, data: dict,
                        expected_revision: int, author: str) -> dict:
    owner = ensure_root(session, ontology_id)
    revision_check(owner, expected_revision)
    resolve_entity(session, owner, data["source_id"])
    resolve_entity(session, owner, data["target_id"])
    if data.get("cardinality", "many") not in CARDINALITY:
        raise HTTPException(422, "Invalid cardinality")
    session.add(OntologyRelationship(root_id=owner.id, **data))
    snapshot(session, owner, author)
    session.commit()
    return graph(session, ontology_id)

def update_relationship(session: Session, ontology_id: int, relationship_id: str, data: dict,
                        expected_revision: int, author: str) -> dict:
    owner = ensure_root(session, ontology_id)
    revision_check(owner, expected_revision)
    row = session.get(OntologyRelationship, relationship_id)
    if row is None or row.root_id != owner.id:
        raise HTTPException(404, "Relationship not found")
    for field in ("source_id", "target_id"):
        if field in data:
            resolve_entity(session, owner, data[field])
    for key, value in data.items():
        setattr(row, key, value)
    if row.cardinality not in CARDINALITY:
        raise HTTPException(422, "Invalid cardinality")
    snapshot(session, owner, author)
    session.commit()
    return graph(session, ontology_id)

def delete_relationship(session: Session, ontology_id: int, relationship_id: str,
                        expected_revision: int, author: str) -> dict:
    owner = ensure_root(session, ontology_id)
    revision_check(owner, expected_revision)
    row = session.get(OntologyRelationship, relationship_id)
    if row is None or row.root_id != owner.id:
        raise HTTPException(404, "Relationship not found")
    session.delete(row)
    snapshot(session, owner, author)
    session.commit()
    return graph(session, ontology_id)

def apply_proposals(session: Session, run: OntologyLearnRun, decisions: dict,
                    expected_revision: int, author: str) -> dict:
    owner = ensure_root(session, run.ontology_id)
    revision_check(owner, expected_revision)
    if run.status != "awaiting_review":
        raise HTTPException(409, "Run is not awaiting review")
    if set(decisions) != {p["id"] for p in run.proposals}:
        raise HTTPException(422, "Every proposal requires an explicit accept or reject decision")
    if set(decisions.values()) - {"accept", "reject"}:
        raise HTTPException(422, "Invalid review decision")
    if any(decisions[p["id"]] == "accept" and p.get("conflict") for p in run.proposals):
        raise HTTPException(422, "Resolve conflicting proposals before accepting them")
    by_name = {e.name.casefold(): e for e in session.scalars(select(OntologyEntity).where(
        OntologyEntity.root_id == owner.id, OntologyEntity.deleted.is_(False)))}
    # Never silently discard accepted fields or relationships whose entity types were rejected.
    available_names = set(by_name)
    available_names.update(p["name"].casefold() for p in run.proposals
                           if p["kind"] == "entity" and decisions[p["id"]] == "accept")
    for proposal in run.proposals:
        if decisions[proposal["id"]] != "accept":
            continue
        refs = ([proposal.get("entity", "")] if proposal["kind"] == "property"
                else [proposal.get("source", ""), proposal.get("target", "")]
                if proposal["kind"] == "relationship" else [])
        if any(not isinstance(ref, str) or ref.casefold() not in available_names for ref in refs):
            raise HTTPException(422, "Accepted proposal references a missing or rejected entity")

    # Entity types first: source order must never prevent property/edge creation.
    ordered = sorted(run.proposals, key=lambda p: {"entity": 0, "property": 1, "relationship": 2}[p["kind"]])
    for p in ordered:
        if decisions[p["id"]] != "accept":
            continue
        if p["kind"] == "entity":
            existing = by_name.get(p["name"].casefold())
            if existing:
                existing.evidence = list(existing.evidence or []) + [x for x in p.get("evidence", []) if x not in (existing.evidence or [])]
            else:
                e = OntologyEntity(root_id=owner.id, name=p["name"], description=p.get("description", ""),
                                   evidence=p.get("evidence", []))
                session.add(e)
                session.flush()
                by_name[e.name.casefold()] = e
        elif p["kind"] == "property":
            e = by_name.get(p["entity"].casefold())
            if e and p["data_type"] in TYPES:
                existing = session.scalar(select(OntologyProperty).where(
                    OntologyProperty.entity_id == e.id, OntologyProperty.name == p["name"]))
                if existing is not None and existing.data_type != p["data_type"]:
                    raise HTTPException(409, "Property type conflict; edit the existing property explicitly")
                if existing is None:
                    values = normalized_property_data({
                        "name": p["name"], "data_type": p["data_type"],
                        "description": p.get("description", ""),
                        "semantic_type": p.get("semantic_type"),
                        "definition": p.get("definition") or {},
                        "value_schema": p.get("value_schema") or {},
                        "evidence": p.get("evidence", []),
                    })
                    session.add(OntologyProperty(entity_id=e.id, **values))
        elif p["kind"] == "relationship":
            source, target = by_name.get(p["source"].casefold()), by_name.get(p["target"].casefold())
            if source and target:
                existing = session.scalar(select(OntologyRelationship).where(
                    OntologyRelationship.root_id == owner.id, OntologyRelationship.source_id == source.id,
                    OntologyRelationship.target_id == target.id, OntologyRelationship.name == p["name"]))
                if existing is None:
                    session.add(OntologyRelationship(root_id=owner.id, source_id=source.id,
                        target_id=target.id, name=p["name"], evidence=p.get("evidence", [])))
    snapshot(session, owner, author)
    run.proposals = [{**p, "decision": decisions[p["id"]]} for p in run.proposals]
    run.applied_revision = owner.revision
    run.status = "succeeded"
    run.finished_at = datetime.now(timezone.utc)
    for source in run.input_sources:
        row = session.scalar(select(OntologyLearnSource).where(
            OntologyLearnSource.ontology_id == run.ontology_id,
            OntologyLearnSource.source_id == source["source_id"]))
        if row is None:
            row = OntologyLearnSource(ontology_id=run.ontology_id, source_id=source["source_id"],
                document_id=source["document_id"], content_digest=source["content_digest"], run_id=run.id)
            session.add(row)
        else:
            row.document_id = source["document_id"]
            row.content_digest = source["content_digest"]
            row.run_id = run.id
            row.reviewed_at = datetime.now(timezone.utc)
    session.commit()
    return graph(session, run.ontology_id)
