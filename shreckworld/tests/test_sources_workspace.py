"""Regression tests for source organization, safety and tree invariants."""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import ShreckWorldLibraryItem, SourceHierarchy
from app.repositories.source_scope import OntologyScope
from app.schemas.sources import (
    AuthoredContent, OrganizationInput, SourceCreate, SourceSection,
)
from app.services.source_workspace import (
    create_authored_source, get_organization, save_organization,
)


def section(id: str, parent: str | None, order: int = 0) -> dict:
    return {
        "id": id, "parent_id": parent, "section_type": "Chapter",
        "title": id, "order": order,
        "content_reference": {"kind": "authored", "paragraphs": []},
    }


def test_tree_rejects_cycles_or_orphans():
    with pytest.raises(ValidationError):
        OrganizationInput(expected_revision=1, nodes=[
            section("root", None), section("a", "b"), section("b", "a"),
        ])
    with pytest.raises(ValidationError):
        OrganizationInput(expected_revision=1, nodes=[
            section("root", None), section("a", "unknown"),
        ])


def test_tree_rejects_duplicate_orders_and_paragraphs():
    with pytest.raises(ValidationError):
        OrganizationInput(expected_revision=1, nodes=[
            section("root", None), section("a", "root"), section("b", "root"),
        ])
    first, second = section("a", "root", 0), section("b", "root", 1)
    first["content_reference"]["paragraphs"] = [{"id": "p1", "text": "A"}]
    second["content_reference"]["paragraphs"] = [{"id": "p1", "text": "B"}]
    with pytest.raises(ValidationError):
        OrganizationInput(expected_revision=1, nodes=[
            section("root", None), first, second,
        ])


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(SourceHierarchy(
            id="rulebook", version=1, name="Rulebook",
            section_types=["Rulebook", "Chapter"], archived=False,
        ))
        session.commit()
        yield session
    engine.dispose()


def test_authored_source_revision_and_world_scoping(db: Session):
    world = OntologyScope(7, "Test World")
    item = create_authored_source(
        db, world, SourceCreate(
            title="First Adventure", source_type="narrative",
            hierarchy_template_id="rulebook", hierarchy_template_version=1,
        ),
    )
    assert item.ontology_id == 7 and item.authored
    original = get_organization(db, item)
    assert len(original.nodes) == 1
    root = original.nodes[0]
    nodes = [root, SourceSection(
        id=f"section-{uuid4()}", parent_id=root.id,
        title="Opening", section_type="Story", order=0,
        content_reference=AuthoredContent(paragraphs=[{"id": "p1", "text": "Once upon a time."}]),
    )]
    saved = save_organization(db, item, OrganizationInput(
        expected_revision=original.revision, nodes=nodes,
    ))
    assert saved.revision == original.revision + 1
    assert get_organization(db, item).nodes[1].content_reference.paragraphs[0].text == "Once upon a time."
    with pytest.raises(HTTPException) as stale:
        save_organization(db, item, OrganizationInput(
            expected_revision=original.revision, nodes=nodes,
        ))
    assert stale.value.status_code == 409
