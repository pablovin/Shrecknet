import pytest

from app.jobs.novelist.block_planner import plan_blocks
from app.jobs.novelist.evidence_ledger import LedgerFact, LedgerScene, NarrativeEvidenceLedger, SourceSegment, validate_provenance
from app.jobs.novelist.prose_quality import validate_prose_html


def _scene(number: int, weight: int = 3) -> LedgerScene:
    return LedgerScene(scene_id=f"scene-{number:03d}", chronology=number, narrative_weight=weight, facts=[LedgerFact(claim="A supported fact", classification="explicit", source_ids=["source-0001"])])


def test_ledger_rejects_claim_without_source_provenance() -> None:
    ledger = NarrativeEvidenceLedger(scenes=[_scene(1)])
    assert validate_provenance(ledger, [SourceSegment(id="source-0001", text="evidence")]) is ledger
    ledger.scenes[0].facts[0].source_ids = ["missing"]
    with pytest.raises(ValueError, match="Unknown source IDs"):
        validate_provenance(ledger, [SourceSegment(id="source-0001", text="evidence")])


def test_blocks_are_chronological_adjacent_and_bounded() -> None:
    blocks = plan_blocks([_scene(3), _scene(1), _scene(2)])
    assert [scene_id for block in blocks for scene_id in block.scene_ids] == ["scene-001", "scene-002", "scene-003"]
    assert all(1200 <= block.target_words <= 1800 for block in blocks)


def test_quality_gate_rejects_lists_repetition_and_short_paragraph_cascade() -> None:
    bad = "<p>Small.</p><p>Small.</p><p>Small.</p>\n- list item"
    errors = validate_prose_html(bad)
    assert "markdown-style list" in errors
    assert "repeated paragraphs" in errors
    assert "three consecutive very short paragraphs" in errors


def test_quality_gate_accepts_normal_html_prose() -> None:
    html = "<p>" + "word " * 45 + "</p><p>" + "other " * 48 + "</p>"
    assert validate_prose_html(html) == []
