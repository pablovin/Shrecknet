"""Ontology Studio domain operations. All calls must be scoped by authorized ontology_id."""
from __future__ import annotations
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.ontology_studio import (WorldOntology, OntologyEntity, OntologyProperty,
    OntologyRelationship, OntologyRevision, OntologyLearnRun, OntologyLearnSource)

TYPES = {"text", "integer", "number", "boolean", "date", "datetime", "json", "image", "url"}
CARDINALITY = {"one", "many"}

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
                        "constraints": p.constraints, "evidence": p.evidence} for p in props],
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
    p = OntologyProperty(entity_id=entity_id, **data)
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
    by_name = {e.name.casefold(): e for e in session.scalars(select(OntologyEntity).where(
        OntologyEntity.root_id == owner.id, OntologyEntity.deleted.is_(False)))}
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
                    session.add(OntologyProperty(entity_id=e.id, name=p["name"], data_type=p["data_type"],
                        description=p.get("description", ""), evidence=p.get("evidence", [])))
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
