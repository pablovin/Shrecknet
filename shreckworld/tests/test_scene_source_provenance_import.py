"""Scene provenance migration: exact evidence only, repeatable and non-destructive."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from app.services.scene_source_provenance_import import (
    _historical_scene_ranges, _paragraphs_from_proposal, _reconciled_payload,
)


def _proposal(scene_id="scene-1", source_id="story-1", start=12, end=19):
    return {"scene_id": scene_id, "source_entity_instance_id": source_id,
            "start_paragraph": start, "end_paragraph": end,
            "source_paragraphs_absolute": [start, start + 1, end]}


def test_only_valid_scene_ranges_are_accepted():
    assert _paragraphs_from_proposal(_proposal()) == {
        "start_paragraph": 12, "end_paragraph": 19,
        "source_paragraphs_absolute": [12, 13, 19]}
    assert _paragraphs_from_proposal({"start_paragraph": None, "end_paragraph": None}) is None
    assert _paragraphs_from_proposal(_proposal(start=19, end=12)) is None
    assert _paragraphs_from_proposal({"start_paragraph": 1, "end_paragraph": 5,
                                        "source_paragraphs_absolute": [6]}) is None


def test_missing_evidence_creates_document_only_reference():
    payload, recovered = _reconciled_payload(
        previous=None, entity_id="story-1",
        document=("source-A", "section-B", "md-C"), historical=None)
    assert recovered is False
    assert payload["entity_instance_id"] == "story-1"
    assert (payload["source_id"], payload["section_id"], payload["markdown_asset_id"]) == (
        "source-A", "section-B", "md-C")
    assert "start_paragraph" not in payload
    assert "end_paragraph" not in payload
    assert payload["provenance_status"] == "document_only"


def test_exact_evidence_is_recovered_once_and_manual_edits_never_overwritten():
    evidence = {"start_paragraph": 12, "end_paragraph": 19,
                "source_paragraphs_absolute": [12, 19]}
    original, recovered = _reconciled_payload(
        previous=None, entity_id="story-1", document=("source-A", "section-B", None),
        historical=evidence)
    assert recovered and original["start_paragraph"] == 12
    again, recovered = _reconciled_payload(
        previous=json.dumps(original), entity_id="story-1",
        document=("source-A", "section-B", None), historical=evidence)
    assert again == original and not recovered
    conflicted, recovered = _reconciled_payload(
        previous=json.dumps({"source_id": "user-overridden", "entity_instance_id": "story-1"}),
        entity_id="story-1", document=("source-A", "section-B", None), historical=evidence)
    assert conflicted is None and not recovered


def test_archived_and_local_artifacts_recover_after_jobs_purge(tmp_path):
    jobs = tmp_path / "shrecknet_jobs.db"
    (tmp_path / "architect_provenance").mkdir()
    analysis = {
        "ontology_instance_id": "instance-1",
        "outputs": {"scenes": [_proposal()]},
    }
    (tmp_path / "architect_provenance" / "analysis-job-7.json").write_text(
        json.dumps(analysis), encoding="utf-8")
    lookup, scanned = _historical_scene_ranges(jobs)
    assert scanned == 1
    assert lookup[("instance-1", "scene-1", "story-1")]["start_paragraph"] == 12

    # A second, disagreeing run cannot silently overwrite historical ranges.
    artifact = tmp_path / "local_tests" / "arhictect" / "Analyses" / "run-8"
    artifact.mkdir(parents=True)
    altered = {"ontology_instance_id": "instance-1",
               "outputs": {"scenes": [_proposal(start=13, end=20)]}}
    (artifact / "pipeline_output.json").write_text(json.dumps(altered), encoding="utf-8")
    lookup, scanned = _historical_scene_ranges(jobs)
    assert scanned == 2
    assert ("instance-1", "scene-1", "story-1") not in lookup
