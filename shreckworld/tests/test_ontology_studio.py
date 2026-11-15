"""Focused Ontology Studio regression tests: persistence, isolation and review."""
from __future__ import annotations
import ast
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.db.base import Base
from app.models.ontology_studio import (WorldOntology, OntologyEntity, OntologyProperty,
    OntologyRelationship, OntologyLearnRun, OntologyLearnSource, OntologyLearnWindow,
    OntologyLearnChapter)
from app.services import ontology_studio as studio


def test_sources_router_imports_every_book_map_dependency():
    router_path = Path(__file__).parents[1] / "app/api/routers/ontology_studio.py"
    tree = ast.parse(router_path.read_text())
    imported_names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                      for alias in node.names}
    assert {"read_book_map", "ShreckWorldDocumentBlock"} <= imported_names

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


def test_deleting_run_clears_its_chapter_checkpoints_and_reuse_cache(db):
    run = OntologyLearnRun(ontology_id=11, owner_id="admin", idempotency_key="delete-me",
        input_sources=[{"source_id": "source", "document_id": "doc", "content_digest": "a" * 64,
                        "chapter_ids": ["chapter"]}], base_revision=0, model_targets={},
        status="awaiting_review", proposals=[{"id": "proposal", "kind": "entity", "name": "Skill"}])
    other = OntologyLearnRun(ontology_id=11, owner_id="admin", idempotency_key="keep-me",
        input_sources=[], base_revision=0, model_targets={}, status="failed", proposals=[])
    other_chapter_run = OntologyLearnRun(ontology_id=11, owner_id="admin", idempotency_key="same-content",
        input_sources=[], base_revision=0, model_targets={}, status="failed", proposals=[])
    db.add_all([run, other, other_chapter_run])
    db.flush()
    for owner_run in (run, other):
        db.add(OntologyLearnWindow(run_id=owner_run.id, ontology_id=11, source_id="source",
            document_id="doc", content_digest="a" * 64, chapter_id="chapter",
            chapter_title="Chapter", window_index=1, fingerprint="f" * 64,
            contract_version="ontology-schema-v3", status="completed", proposals=[]))
    db.add(OntologyLearnWindow(run_id=other_chapter_run.id, ontology_id=11, source_id="source",
        document_id="doc", content_digest="a" * 64, chapter_id="duplicate-content-chapter",
        chapter_title="Duplicate content", window_index=1, fingerprint="f" * 64,
        contract_version="ontology-schema-v3", status="completed", proposals=[]))
    db.add(OntologyLearnChapter(ontology_id=11, source_id="source", document_id="doc",
        content_digest="a" * 64, chapter_id="chapter", chapter_title="Chapter"))
    db.commit()

    result = studio.delete_run(db, run)

    assert result == {"deleted_runs": 1, "cleared_checkpoints": 3, "cleared_chapters": 1}
    assert db.get(OntologyLearnRun, run.id) is None
    assert list(db.scalars(select(OntologyLearnWindow).where(
        OntologyLearnWindow.ontology_id == 11))) == []
    assert list(db.scalars(select(OntologyLearnChapter).where(
        OntologyLearnChapter.ontology_id == 11))) == []
    assert db.get(OntologyLearnRun, other.id) is not None
    assert db.get(OntologyLearnRun, other_chapter_run.id) is not None


def test_active_run_cannot_be_deleted(db):
    run = OntologyLearnRun(ontology_id=12, owner_id="admin", idempotency_key="active",
        input_sources=[], base_revision=0, model_targets={}, status="running", proposals=[])
    db.add(run)
    db.commit()
    with pytest.raises(HTTPException) as error:
        studio.delete_run(db, run)
    assert error.value.status_code == 409

def test_human_draft_proposals_are_revisioned_and_removable(db, monkeypatch):
    # API router package initializes the document pipeline, which is optional in focused CI.
    pytest.importorskip("fitz")
    from app.api.routers import ontology_studio as router
    monkeypatch.setattr(router, "auth", lambda *_: None)
    run = OntologyLearnRun(ontology_id=15, owner_id="admin", idempotency_key=str(uuid4()),
        input_sources=[], base_revision=0, model_targets={}, status="awaiting_review",
        proposals=[{"id": "entity", "kind": "entity", "name": "Skill", "evidence": []},
                   {"id": "prop", "kind": "property", "name": "value", "entity": "Skill",
                    "data_type": "integer", "evidence": []},
                   {"id": "edge", "kind": "relationship", "name": "LEARNED",
                    "source": "Skill", "target": "Skill", "evidence": []}])
    db.add(run)
    db.commit()
    created = router.create_proposal(15, run.id,
        router.ProposalCreate(expected_proposal_revision=0, kind="entity", name="Investigator"),
        token="testing", db=db)
    assert created["proposal_revision"] == 1
    new_id = next(p["id"] for p in created["proposals"] if p["name"] == "Investigator")
    assert next(p for p in created["proposals"] if p["id"] == new_id)["created_by_human"]
    with pytest.raises(HTTPException) as stale:
        router.create_proposal(15, run.id,
            router.ProposalCreate(expected_proposal_revision=0, kind="entity", name="Actor"),
            token="testing", db=db)
    assert stale.value.status_code == 409
    updated = router.edit_proposal(15, run.id, "entity",
        router.ProposalEdit(expected_proposal_revision=1, name="Ability"),
        token="testing", db=db)
    by_id = {p["id"]: p for p in updated["proposals"]}
    assert by_id["prop"]["entity"] == "Ability"
    assert by_id["edge"]["source"] == "Ability"
    assert by_id["edge"]["target"] == "Ability"
    with pytest.raises(HTTPException) as extracted:
        router.delete_proposal(15, run.id, "entity", expected_proposal_revision=2,
                               token="testing", db=db)
    assert extracted.value.status_code == 422
    result = router.delete_proposal(15, run.id, new_id, expected_proposal_revision=2,
                                    token="testing", db=db)
    assert new_id not in {p["id"] for p in result["proposals"]}


def test_accepted_definitions_cannot_reference_rejected_entities(db):
    run = OntologyLearnRun(ontology_id=19, owner_id="admin", idempotency_key=str(uuid4()),
        input_sources=[], base_revision=0, model_targets={}, status="awaiting_review",
        proposals=[{"id": "entity", "kind": "entity", "name": "Skill", "evidence": []},
                   {"id": "property", "kind": "property", "name": "value",
                    "entity": "Skill", "data_type": "integer", "evidence": []}])
    db.add(run)
    db.commit()
    with pytest.raises(HTTPException) as error:
        studio.apply_proposals(db, run, {"entity": "reject", "property": "accept"}, 0, "admin")
    assert error.value.status_code == 422
    assert studio.graph(db, 19)["entities"] == []
