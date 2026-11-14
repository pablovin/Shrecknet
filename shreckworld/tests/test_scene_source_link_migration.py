"""Manual Scene/Milestone Source reference migration helpers."""
from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.shreckworld import ShreckWorldLibraryItem
from app.models.source_collection import SourceCollection
from app.models.sources import SourceAsset
from app.services.scene_source_link_migration import (
    _candidate_source,
    _payload_with_source,
    _source_references,
    _write_target_batch,
)


def test_collection_entry_source_must_resolve_uniquely():
    references = {
        (7, "entry-a"): [{"source_id": "source-a", "source_title": "Story A"}],
        (8, "entry-a"): [{"source_id": "source-b", "source_title": "Story A"}],
        (7, "entry-many"): [
            {"source_id": "source-c", "source_title": "Story C"},
            {"source_id": "source-d", "source_title": "Story D"},
        ],
    }

    match, reason = _candidate_source(references, 7, "entry-a")
    assert match["source_id"] == "source-a"
    assert reason is None
    assert _candidate_source(references, 7, "missing") == (None, "no_existing_source_for_collection_entry")
    assert _candidate_source(references, 7, "entry-many") == (None, "multiple_sources_for_collection_entry")
    assert _candidate_source(references, 8, "entry-a")[0]["source_id"] == "source-b"


def test_existing_scene_passage_evidence_survives_source_repair():
    original = {
        "entity_instance_id": "legacy-story",
        "source_id": "stale-source",
        "section_id": "stale-section",
        "start_paragraph": 4,
        "end_paragraph": 6,
        "source_paragraphs_absolute": [4, 6],
        "provenance_status": "historical_unverified",
    }
    repaired = json.loads(_payload_with_source(
        json.dumps(original), entity_id="legacy-story",
        source={"source_id": "source-new", "section_id": "section-new"},
    ))

    assert repaired["source_id"] == "source-new"
    assert repaired["section_id"] == "section-new"
    assert repaired["start_paragraph"] == 4
    assert repaired["end_paragraph"] == 6
    assert repaired["source_paragraphs_absolute"] == [4, 6]
    assert repaired["provenance_status"] == "historical_unverified"


def test_new_scene_gets_document_only_source_reference():
    repaired = json.loads(_payload_with_source(
        None, entity_id="legacy-story",
        source={"source_id": "source-new", "section_id": "section-new"},
    ))

    assert repaired == {
        "entity_instance_id": "legacy-story",
        "source_id": "source-new",
        "section_id": "section-new",
        "provenance_status": "document_only",
    }


def test_historical_scene_evidence_is_added_without_claiming_current_revision():
    repaired = json.loads(_payload_with_source(
        None, entity_id="legacy-story",
        source={"source_id": "source-new", "section_id": "section-new"},
        historical={
            "start_paragraph": 12, "end_paragraph": 19,
            "source_paragraphs_absolute": [12, 13, 19],
        },
    ))
    assert repaired["start_paragraph"] == 12
    assert repaired["end_paragraph"] == 19
    assert repaired["source_paragraphs_absolute"] == [12, 13, 19]
    assert repaired["provenance_status"] == "historical_unverified"


def test_collection_entry_resolves_only_existing_source_in_same_ontology(tmp_path):
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "source-map.db"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add_all([
            ShreckWorldLibraryItem(
                id="source-7", ontology_id=7, title="Story Seven", source_type="narrative",
                pdf_path="", source_sha256="0" * 64,
            ),
            ShreckWorldLibraryItem(
                id="source-8", ontology_id=8, title="Story Eight", source_type="narrative",
                pdf_path="", source_sha256="0" * 64,
            ),
            SourceAsset(
                id="asset-7", source_id="source-7", section_id="section-7",
                kind="markdown", storage_path="/tmp/story.md", mime_type="text/markdown",
                content_sha256="0" * 64,
            ),
            SourceCollection(
                id="collection-7", ontology_id=7, source_type="narrative",
                hierarchy_template_id="template-7", hierarchy_template_version=1,
                name="Stories", nodes=[{"id": "entry-7", "source_ids": ["source-7", "source-8"]}],
            ),
        ])
        db.commit()

    references = _source_references(factory)
    match, reason = _candidate_source(references, 7, "entry-7")
    assert reason is None
    assert match == {
        "source_id": "source-7",
        "source_title": "Story Seven",
        "section_id": "section-7",
        "markdown_asset_id": "asset-7",
        "section_ambiguous": False,
    }


def test_graph_write_adds_source_properties_without_mutating_legacy_relationships():
    class Result:
        def single(self):
            return {"updated": 1}

    class Transaction:
        def __init__(self):
            self.queries = []

        def run(self, query, **kwargs):
            self.queries.append(query)
            assert len(kwargs["records"]) == 1
            return Result()

    tx = Transaction()
    _write_target_batch(tx, [
        {"id": "scene-1", "kind": "Scene", "ontology_id": 7,
         "instance_id": "instance-1", "parent_scene_id": None,
         "entity_id": "legacy-entity", "source_id": "source-1",
         "section_id": "section-1", "markdown_asset_id": None,
         "provenance": '{"entity_instance_id":"legacy-entity","source_id":"source-1"}'},
        {"id": "milestone-1", "kind": "Milestone", "ontology_id": 7,
         "instance_id": "instance-1", "parent_scene_id": "scene-1",
         "entity_id": "legacy-entity", "source_id": "source-1",
         "section_id": "section-1", "markdown_asset_id": None,
         "provenance": None},
    ])
    query = "\n".join(tx.queries).upper()
    assert "SET NODE.SOURCE_ID" in query
    assert len(tx.queries) == 2
    assert "DERIVED_FROM" in query
    assert "DELETE" not in query
    assert "CREATE" not in query
    assert "MERGE" not in query
