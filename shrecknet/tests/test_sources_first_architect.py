"""Source-first Architect contracts and secured Markdown read tests."""
from hashlib import sha256
import sqlite3

import pytest
from pydantic import ValidationError

from app.schemas.ontology_instance import SceneDerivedFrom, MilestoneDerivedFrom
from app.services.architect_source_documents import (
    list_architect_sources, load_architect_source, _media_file,
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
