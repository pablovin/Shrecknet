"""Source-first Architect contracts and secured Markdown read tests."""
from hashlib import sha256
import sqlite3

import pytest
from pydantic import ValidationError

from app.schemas.ontology_instance import SceneDerivedFrom, MilestoneDerivedFrom
from app.services.architect_passages import milestone_source_passage
from app.services.architect_source_documents import (
    list_architect_sources, list_architect_source_catalog, load_architect_source, _media_file,
)


def _fixture(tmp_path, monkeypatch):
    media = tmp_path / "media"
    saved = media / "ontologies" / "42" / "source-1" / "story.md"
    saved.parent.mkdir(parents=True)
    payload = b"# The Journey\n\nFirst paragraph.\n\nSecond paragraph.\n"
    saved.write_bytes(payload)
    monkeypatch.setenv("SHRECKNET_SHRECKWORLD_MEDIA_PATH", str(media))
    db = tmp_path / "shreckworld.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE shreckworld_library_items (
                id TEXT PRIMARY KEY, ontology_id INTEGER, title TEXT,
                source_revision INTEGER, storage_path TEXT,
                mime_type TEXT, source_sha256 TEXT, source_type TEXT);
            CREATE TABLE source_assets (
                id TEXT, source_id TEXT, section_id TEXT,
                kind TEXT, storage_path TEXT, content_sha256 TEXT);
            CREATE TABLE source_organizations (
                source_id TEXT, revision INTEGER, nodes TEXT);
            CREATE TABLE graph_source_imports (
                ontology_id INTEGER, instance_id TEXT, entity_instance_id TEXT,
                source_id TEXT, section_id TEXT);
        """)
        conn.execute("INSERT INTO shreckworld_library_items VALUES(?,?,?,?,?,?,?,?)",
            ("source-1", 42, "The Journey", 5, "/app/media/ontologies/42/source-1/story.md",
             "text/markdown", sha256(payload).hexdigest(), "narrative"))
        conn.execute("INSERT INTO source_assets VALUES(?,?,?,?,?,?)",
            ("asset-1", "source-1", "chapter-1", "markdown",
             "/app/media/ontologies/42/source-1/story.md", sha256(payload).hexdigest()))
        conn.execute("INSERT INTO graph_source_imports VALUES(?,?,?,?,?)",
            (42, "page-1", "legacy-story", "source-1", "chapter-1"))
        conn.execute("INSERT INTO source_organizations VALUES(?,?,?)",
            ("source-1", 5, '[{"id":"chapter-1","metadata":{"markdown_asset_id":"asset-1"}}]'))
    return db, saved


def test_reader_only_lists_file_backed_narrative_sources(tmp_path, monkeypatch):
    db, saved = _fixture(tmp_path, monkeypatch)
    entries = list_architect_sources(42, db)
    assert len(entries) == 1
    assert entries[0].source_id == "source-1"
    assert entries[0].section_id == "chapter-1"
    assert entries[0].legacy_instance_id == "page-1"
    assert entries[0].source_revision == 5
    assert list_architect_sources(99, db) == []
    # A stale Markdown asset must not become a selectable document.
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO source_assets VALUES(?,?,?,?,?,?)",
            ("orphan", "source-1", "removed-section", "markdown",
             "/app/media/ontologies/42/source-1/story.md", sha256(saved.read_bytes()).hexdigest()))
    assert len(list_architect_sources(42, db)) == 1
    doc, text = load_architect_source(42, "source-1", "chapter-1", db)
    assert doc.markdown_asset_id == "asset-1"
    assert "Second paragraph" in text
    with pytest.raises(ValueError):
        load_architect_source(42, "source-1", "wrong-section", db)
    saved.write_text("modified but not saved", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        load_architect_source(42, "source-1", "chapter-1", db)


def test_file_path_cannot_escape_shared_source_media(tmp_path):
    with pytest.raises(ValueError):
        _media_file("/etc/passwd", tmp_path)
    with pytest.raises(ValueError):
        _media_file("/app/media/../../etc/passwd", tmp_path)


def test_scene_and_milestone_can_use_source_without_graph_entity():
    scene = SceneDerivedFrom(
        source_id="source-1", section_id="chapter-1",
        markdown_asset_id="asset-1", source_revision=5,
        start_paragraph=2, end_paragraph=3,
        source_paragraphs_absolute=[2, 3], provenance_status="architect",
    )
    assert scene.entity_instance_id is None
    milestone = MilestoneDerivedFrom(
        source_id="source-1", section_id="chapter-1",
        start_paragraph=2, end_paragraph=3,
        provenance_status="scene_inherited",
    )
    assert milestone.entity_instance_id is None
    assert SceneDerivedFrom(entity_instance_id="legacy").source_id is None
    assert MilestoneDerivedFrom(entity_instance_id="legacy").source_id is None
    with pytest.raises(ValidationError):
        SceneDerivedFrom(section_id="orphan-section")
    with pytest.raises(ValidationError):
        MilestoneDerivedFrom(source_id="source-1")


def test_milestone_specific_paragraph_citation_is_preserved():
    scene = {"start_paragraph": 10, "end_paragraph": 15,
             "source_paragraphs_absolute": [10, 11, 12, 13, 14, 15]}
    exact = {"source_paragraphs_absolute": [11, 13], "provenance_status": "architect"}
    assert milestone_source_passage(exact, scene) == {
        "start_paragraph": 11, "end_paragraph": 13,
        "source_paragraphs_absolute": [11, 13],
        "provenance_status": "architect",
    }
    # An unrelated paragraph marker cannot masquerade as passage evidence.
    forged = {"source_paragraphs_absolute": [11, 99], "provenance_status": "architect"}
    inherited = milestone_source_passage(forged, scene)
    assert inherited["provenance_status"] == "scene_inherited"
    assert inherited["start_paragraph"] == 10
    assert inherited["end_paragraph"] == 15
    assert milestone_source_passage({}, {})["provenance_status"] == "document_only"
    assert milestone_source_passage(exact, {"start_paragraph": "invalid",
            "end_paragraph": 20})["start_paragraph"] is None


def test_partial_legacy_source_references_remain_readable():
    legacy_scene = SceneDerivedFrom(
        entity_instance_id="entity-1", source_id="source-1",
        provenance_status="document_only",
    )
    legacy_milestone = MilestoneDerivedFrom(
        entity_instance_id="entity-1", source_id="source-1",
        provenance_status="document_only",
    )
    assert legacy_scene.section_id is None
    assert legacy_milestone.section_id is None


def test_catalog_recovers_full_document_when_only_stale_asset_exists(tmp_path, monkeypatch):
    db, saved = _fixture(tmp_path, monkeypatch)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE source_organizations SET nodes='[]'")
    entries = list_architect_sources(42, db)
    assert len(entries) == 1
    assert entries[0].section_id == "source-source-1"
    assert entries[0].markdown_asset_id is None
    assert load_architect_source(42, "source-1", "source-source-1", db)[1].startswith("# The Journey")


def test_catalog_uses_collection_title_and_reports_missing_file(tmp_path, monkeypatch):
    db, saved = _fixture(tmp_path, monkeypatch)
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE source_collections (
            id TEXT, name TEXT, nodes TEXT, ontology_id INTEGER, source_type TEXT, archived INTEGER
        )""")
        conn.execute("INSERT INTO source_collections VALUES(?,?,?,?,?,?)", (
            "c", "Stories",
            '[{"id":"a","title":"Adventure","parent_id":null,"source_ids":[]},'
            '{"id":"b","title":"The Vampire Sol","parent_id":"a","source_ids":["source-1"]}]',
            42, "narrative", 0))
        conn.execute("UPDATE shreckworld_library_items SET title='Page 123abc'")
    entry = list_architect_source_catalog(42, db)[0]
    assert entry["title"] == "The Vampire Sol"
    assert entry["hierarchy"] == ["Adventure", "The Vampire Sol"]
    assert entry["readiness"] == "ready"
    assert list_architect_source_catalog(99, db) == []
    saved.unlink()
    entry = list_architect_source_catalog(42, db)[0]
    assert entry["readiness"] == "unavailable"
    assert entry["readiness_reason"]
    assert list_architect_sources(42, db) == []
