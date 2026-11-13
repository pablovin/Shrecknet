"""Collection model contracts: template assignment, nested cards, attachments and images."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import SourceHierarchy, ShreckWorldLibraryItem
from app.repositories.source_scope import OntologyScope
from app.schemas.source_collections import CollectionCreate, CollectionEntry, CollectionUpdate, CollectionTemplateUpdate
from app.services import source_collections


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(SourceHierarchy(
            id="adventure", version=1, name="Adventures",
            section_types=["Adventure", "Story Arc", "Session"],
            description="Campaign adventures", archived=False,
        ))
        session.commit()
        yield session
    engine.dispose()


def entry(id, parent_id=None, level=0, order=0, sources=None):
    return CollectionEntry(
        id=id, parent_id=parent_id, level=level, order=order, title=id,
        source_ids=sources or [],
    )


def sample(db: Session, world=7, category="narrative"):
    return source_collections.create_collection(
        db, OntologyScope(world, "World"), CollectionCreate(
            source_type=category, hierarchy_template_id="adventure",
            hierarchy_template_version=1,
        ),
    )


def test_world_and_category_scoped_assignments_archive_restore(db):
    first = sample(db)
    other = sample(db, world=8)
    separate = sample(db, world=7, category="explanatory")
    assert first.id != other.id != separate.id
    assert first.section_types == ["Adventure", "Story Arc", "Session"]
    with pytest.raises(HTTPException) as duplicate:
        sample(db)
    assert duplicate.value.status_code == 409
    assert len(source_collections.list_collections(db, OntologyScope(7, "World"), "narrative")) == 1
    with pytest.raises(HTTPException) as forbidden:
        source_collections.scoped_collection(db, OntologyScope(8, "Other"), first.id)
    assert forbidden.value.status_code == 404
    source_collections.remove_collection(db, OntologyScope(7, "World"), first.id, 1)
    restored = sample(db)
    assert restored.id == first.id and restored.revision == 3 and not restored.archived


def test_nested_entry_tree_and_source_references(db):
    col = sample(db)
    src = ShreckWorldLibraryItem(
        id="file-1", ontology_id=7, source_type="narrative",
        title="Battle transcript", pdf_path="/unused",
        source_sha256="a" * 64,
    )
    foreign = ShreckWorldLibraryItem(
        id="file-2", ontology_id=8, source_type="narrative",
        title="Other world", pdf_path="/unused", source_sha256="b" * 64,
    )
    explanatory = ShreckWorldLibraryItem(
        id="file-3", ontology_id=7, source_type="explanatory",
        title="Other category", pdf_path="/unused", source_sha256="c" * 64,
    )
    db.add_all([src, foreign, explanatory]); db.commit()
    nodes = [
        entry("adventure"), entry("arc", "adventure", 1),
        entry("session", "arc", 2, sources=["file-1"]),
    ]
    updated = source_collections.save_collection(
        db, OntologyScope(7, "World"), col.id,
        CollectionUpdate(expected_revision=1, name="Adventures",
                         description="", nodes=nodes),
    )
    assert updated.revision == 2
    assert updated.nodes[2].source_ids == ["file-1"]
    with pytest.raises(HTTPException) as stale:
        source_collections.save_collection(
            db, OntologyScope(7, "World"), col.id,
            CollectionUpdate(expected_revision=1, name="Stale", nodes=nodes),
        )
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as wrong_level:
        source_collections.save_collection(
            db, OntologyScope(7, "World"), col.id,
            CollectionUpdate(expected_revision=2, name="Bad", nodes=[
                entry("adventure"), entry("arc", "adventure", 2),
            ]),
        )
    assert wrong_level.value.status_code == 422
    for source_id in ["file-2", "file-3", "missing"]:
        with pytest.raises(HTTPException) as cross_scope:
            source_collections.save_collection(
                db, OntologyScope(7, "World"), col.id,
                CollectionUpdate(expected_revision=2, name="Bad", nodes=[
                    entry("adventure", sources=[source_id]),
                ]),
            )
        assert cross_scope.value.status_code == 422


def test_invalid_tree_and_duplicate_positions():
    with pytest.raises(ValidationError):
        CollectionUpdate(expected_revision=1, name="Bad", nodes=[
            entry("a", "b"), entry("b", "a", 1),
        ])
    with pytest.raises(ValidationError):
        CollectionUpdate(expected_revision=1, name="Bad", nodes=[
            entry("a"), entry("b"),
        ])


def test_image_upload_changes_only_target_entry_and_keeps_old_revision(db, tmp_path, monkeypatch):
    col = sample(db)
    scope = OntologyScope(7, "World")
    source_collections.save_collection(db, scope, col.id, CollectionUpdate(
        expected_revision=1, name=col.name, nodes=[entry("adventure")],
    ))
    monkeypatch.setattr(source_collections, "get_settings",
                        lambda: SimpleNamespace(media_root=tmp_path))
    updated = source_collections.upload_entry_image(
        db, scope, col.id, "adventure", 2,
        bytes.fromhex("89504e470d0a1a0a") + b"test", "image/png",
    )
    assert updated.revision == 3
    assert updated.nodes[0].image_key.endswith(".png")
    assert source_collections.entry_image(db, scope, col.id, "adventure").exists()
    no_art = source_collections.remove_entry_image(db, scope, col.id, "adventure", 3)
    assert no_art.revision == 4 and no_art.nodes[0].image_key is None
    with pytest.raises(HTTPException) as missing_image:
        source_collections.entry_image(db, scope, col.id, "adventure")
    assert missing_image.value.status_code == 404
    with pytest.raises(HTTPException):
        source_collections.upload_entry_image(
            db, scope, col.id, "adventure", 2, b"evil", "image/png",
        )


def test_sources_migration_seeds_sessions_and_world_lore_templates():
    from app.db.sources import migrate_sources
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        migrate_sources(engine)
        migrate_sources(engine)  # Safe to run repeatedly at service startup.
        with Session(engine) as session:
            adventures = session.get(SourceHierarchy, ("campaign-sessions", 1))
            lore = session.get(SourceHierarchy, ("world-lore", 1))
            assert adventures is not None
            assert adventures.section_types == ["Adventure", "Story Arc", "Session"]
            assert lore is not None
            assert lore.section_types == ["Topic", "Entry", "Section"]
    finally:
        engine.dispose()


def test_explicit_template_version_upgrade_preserves_cards_and_rejects_dropped_levels(db):
    collection = sample(db)
    scope = OntologyScope(7, "World")
    entries = [
        entry("adventure"), entry("arc", "adventure", 1),
        entry("session", "arc", 2),
    ]
    source_collections.save_collection(db, scope, collection.id, CollectionUpdate(
        expected_revision=1, name=collection.name, nodes=entries,
    ))
    db.add_all([
        SourceHierarchy(
            id="adventure", version=2, name="Campaign Adventures",
            section_types=["Adventure", "Plot Arc", "Session"], archived=False,
        ),
        SourceHierarchy(
            id="adventure", version=3, name="Shortened",
            section_types=["Adventure", "Chapter"], archived=False,
        ),
    ])
    db.commit()
    upgraded = source_collections.upgrade_collection_template(
        db, scope, collection.id, CollectionTemplateUpdate(expected_revision=2, template_version=2),
    )
    assert upgraded.revision == 3 and upgraded.section_types == ["Adventure", "Plot Arc", "Session"]
    assert [node.id for node in upgraded.nodes] == ["adventure", "arc", "session"]
    with pytest.raises(HTTPException) as refused:
        source_collections.upgrade_collection_template(
            db, scope, collection.id,
            CollectionTemplateUpdate(expected_revision=3, template_version=3),
        )
    assert refused.value.status_code == 422
