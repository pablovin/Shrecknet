"""Historical Architect paragraph evidence is recovered by exact identity."""
from __future__ import annotations

import json

from app.services.scene_source_provenance_import import (
    _historical_scene_ranges, _paragraphs_from_proposal,
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
