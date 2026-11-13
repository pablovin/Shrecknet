"""Compatibility-first Scene Source provenance tests."""
from __future__ import annotations

from pathlib import Path
import json
import sqlite3

import pytest
from pydantic import ValidationError

from app.schemas.ontology_instance import SceneDerivedFrom
from app.services.scene_source_reference import resolve_source_reference
from app.celery_queue_reaper import _archive_architect_scene_evidence


def test_scene_derived_from_retains_graph_entity_and_optional_document():
    legacy = SceneDerivedFrom(entity_instance_id="story-1")
    assert legacy.source_id is None and legacy.start_paragraph is None
    mapped = SceneDerivedFrom(
        entity_instance_id="story-1", source_id="source-1", section_id="section-1",
        markdown_asset_id="file-1", start_paragraph=12, end_paragraph=19,
        source_paragraphs_absolute=[12, 15, 19], provenance_status="architect")
    assert mapped.entity_instance_id == "story-1"
    assert mapped.source_id == "source-1"
    with pytest.raises(ValidationError):
        SceneDerivedFrom(entity_instance_id="story-1", start_paragraph=19, end_paragraph=12)
    with pytest.raises(ValidationError):
        SceneDerivedFrom(entity_instance_id="story-1", source_paragraphs_absolute=[2, 2])


def test_lookup_uses_migration_ledger_without_modifying_source(tmp_path):
    path = tmp_path / "shreckworld.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE graph_source_imports(
            ontology_id INTEGER, instance_id TEXT, entity_instance_id TEXT,
            source_id TEXT, section_id TEXT);
        CREATE TABLE source_assets(id TEXT, source_id TEXT, section_id TEXT, kind TEXT);
        INSERT INTO graph_source_imports VALUES(42, 'instance-1', 'legacy-story', 'source-A', 'section-B');
        INSERT INTO source_assets VALUES('file-C', 'source-A', 'section-B', 'markdown');
        """)
    resolved = resolve_source_reference(42, "instance-1", "legacy-story", path)
    assert resolved == {"source_id": "source-A", "section_id": "section-B",
                        "markdown_asset_id": "file-C"}
    assert resolve_source_reference(42, "instance-2", "legacy-story", path) == {}
    assert resolve_source_reference(99, "instance-1", "legacy-story", path) == {}
    assert resolve_source_reference(42, "instance-1", "unmapped", path) == {}


def test_architect_analysis_ranges_survive_jobs_startup_cleanup(tmp_path):
    db_path = tmp_path / "shrecknet_jobs.db"
    payload = {
        "pipeline_output": {
            "ontology_instance_id": "instance-1",
            "outputs": {"scenes": [
                {"scene_id": "scene-A", "source_entity_instance_id": "story-1",
                 "start_paragraph": 12, "end_paragraph": 19,
                 "source_paragraphs_absolute": [12, 13, 18, 19]}
            ]}
        }
    }
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE background_jobs (id INTEGER PRIMARY KEY,
                job_type TEXT, details TEXT);
        """)
        conn.execute("INSERT INTO background_jobs VALUES(?, ?, ?)",
                     (7, "architect_analysis", json.dumps(payload)))
        conn.row_factory = sqlite3.Row
        assert _archive_architect_scene_evidence(conn, f"sqlite:///{db_path}") == 1
    archival = db_path.parent / "architect_provenance" / "analysis-job-7.json"
    assert archival.is_file()
    slim = json.loads(archival.read_text(encoding="utf-8"))
    scene = slim["outputs"]["scenes"][0]
    assert scene["source_paragraphs_absolute"] == [12, 13, 18, 19]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        assert _archive_architect_scene_evidence(conn, f"sqlite:///{db_path}") == 0
