"""Focused Ontology Studio regression tests: persistence, isolation and review."""
from __future__ import annotations
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db.base import Base
from app.models.ontology_studio import (WorldOntology, OntologyEntity, OntologyProperty,
    OntologyRelationship, OntologyLearnRun, OntologyLearnSource)
from app.services import ontology_studio as studio

@pytest.fixture
def db(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "ontology.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()

def test_isolated_editing_and_revisions(db):
    initial = studio.graph(db, 7)
    assert initial["revision"] == 0
    first = studio.create_entity(db, 7, {"name": "Character"}, 0, "admin")
    assert first["revision"] == 1
    assert first["entities"][0]["name"] == "Character"
    character = first["entities"][0]["id"]
    second = studio.create_entity(db, 7, {"name": "Skill"}, 1, "admin")
    skill = next(x["id"] for x in second["entities"] if x["name"] == "Skill")
    third = studio.create_property(db, 7, character,
        {"name": "age", "data_type": "integer", "required": False}, 2, "admin")
    assert third["properties"][0]["data_type"] == "integer"
    fourth = studio.create_relationship(db, 7, {"name": "HAS_SKILL",
        "source_id": character, "target_id": skill}, 3, "admin")
    assert len(fourth["relationships"]) == 1
    assert studio.graph(db, 8) == {"revision": 0, "entities": [], "properties": [], "relationships": []}
    with pytest.raises(HTTPException) as stale:
        studio.delete_entity(db, 7, character, expected_revision=3, author="admin")
    assert stale.value.status_code == 409
    result = studio.delete_entity(db, 7, skill, expected_revision=4, author="admin")
    assert len(result["relationships"]) == 0
    assert len(result["entities"]) == 1

def test_cross_world_references_and_cycles_are_rejected(db):
    a = studio.create_entity(db, 7, {"name": "A"}, 0, "admin")
    b = studio.create_entity(db, 8, {"name": "B"}, 0, "admin")
    with pytest.raises(HTTPException) as error:
        studio.create_relationship(db, 7, {"name": "KNOWS", "source_id": a["entities"][0]["id"],
            "target_id": b["entities"][0]["id"]}, 1, "admin")
    assert error.value.status_code == 404
    with pytest.raises(HTTPException):
        studio.update_entity(db, 7, a["entities"][0]["id"],
            {"parent_id": a["entities"][0]["id"]}, 1, "admin")

def test_review_order_and_evidence_retained(db):
    run = OntologyLearnRun(ontology_id=7, owner_id="admin", idempotency_key=str(uuid4()),
         input_sources=[{"source_id": str(uuid4()), "document_id": str(uuid4()),
                         "content_digest": "a"*64}],
         base_revision=0, model_targets={}, status="awaiting_review",
         proposals=[
            {"id": "prop", "kind": "property", "name": "glory",
             "entity": "Character", "data_type": "integer", "evidence": [{"block_ids": ["b1"]}]},
            {"id": "edge", "kind": "relationship", "name": "HAS_SKILL",
             "source": "Character", "target": "Skill", "evidence": []},
            {"id": "character", "kind": "entity", "name": "Character", "evidence": []},
            {"id": "skill", "kind": "entity", "name": "Skill", "evidence": []}
         ])
    db.add(run)
    db.commit()
    result = studio.apply_proposals(db, run, {x["id"]: "accept" for x in run.proposals}, 0, "admin")
    assert len(result["entities"]) == 2
    assert len(result["properties"]) == 1
    assert len(result["relationships"]) == 1
    assert result["properties"][0]["evidence"][0]["block_ids"] == ["b1"]
    learned = db.query(OntologyLearnSource).filter_by(ontology_id=7).one()
    assert learned.run_id == run.id
    assert run.status == "succeeded"

def test_incomplete_review_cannot_mutate(db):
    run = OntologyLearnRun(ontology_id=9, owner_id="admin", idempotency_key=str(uuid4()),
        input_sources=[], base_revision=0, model_targets={}, status="awaiting_review",
        proposals=[{"id": "x", "kind": "entity", "name": "A", "evidence": []}])
    db.add(run)
    db.commit()
    with pytest.raises(HTTPException) as error:
        studio.apply_proposals(db, run, {}, 0, "admin")
    assert error.value.status_code == 422
    assert studio.graph(db, 9)["entities"] == []
