"""Compact V2 contract and reliable provenance regression tests."""
from __future__ import annotations

from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.ontology_studio import OntologyLearnRun, OntologyLearnWindow
from app.services.ontology_compact import (
    CONTRACT_VERSION, DISCOVERY_SCHEMA, CompactContractError, model_window,
    cache_fingerprint, parse_discovery, accumulate, ambiguous_groups,
    reconciliation_input, apply_reconciliation, parse_model_response,
)
from app.services.ontology_discovery_batches import MAX_INPUT_TOKENS, build_discovery_batches
from test_ontology_discovery_batches import make_map, block


def fixture_batch():
    return {
        "chapter_id": "chapter-1", "chapter_title": "Characteristics",
        "sections": [
            {"section_id": "s1", "heading_path": ["Character", "Characteristics"],
             "blocks": [{"id": "long-uuid-1", "text": "Character Strength is a characteristic.",
                         "fragment_index": 0}]},
            {"section_id": "s2", "heading_path": ["Character", "Skills"],
             "blocks": [{"id": "long-uuid-2", "text": "Characters have Skills.",
                         "fragment_index": 0}]},
        ],
    }


def test_numeric_evidence_and_foreign_key_reconstruction():
    prompt, lookup = model_window(fixture_batch(), "RuneQuest")
    assert "long-uuid-1" not in prompt and "long-uuid-2" not in prompt
    assert '"i":0' in prompt and '"i":1' in prompt
    raw = {"e": [{"n": "Character", "b": [0]}, {"n": "Skill", "b": [1]}],
           "p": [{"o": 0, "n": "Strength", "t": "i", "b": [0]}],
           "r": [{"s": 0, "t": 1, "n": "HAS_SKILL", "b": [1]}],
           "overflow": False}
    base = {"source_id": "source-id", "document_id": "doc-id",
            "content_digest": "a" * 64,
            "chapter_id": "chapter-1", "chapter_title": "Characteristics"}
    proposals = parse_discovery(raw, lookup, base)
    assert len(proposals) == 4
    assert proposals[2]["entity"] == "Character"
    assert proposals[2]["data_type"] == "integer"
    assert proposals[2]["description"] == ""
    assert "confidence" not in proposals[2]
    assert proposals[2]["evidence"][0]["block_ids"] == ["long-uuid-1"]
    assert proposals[2]["evidence"][0]["section_ids"] == ["s1"]
    assert proposals[3]["source"] == "Character"
    assert proposals[3]["target"] == "Skill"
    assert proposals[3]["evidence"][0]["block_ids"] == ["long-uuid-2"]
    assert len(prompt) < 600


@pytest.mark.parametrize("payload,code", [
    ({"e": [], "p": [], "r": [], "overflow": True}, "compact_output_overflow"),
    ({"e": [], "p": [], "r": [{"s": 0, "t": 0, "n": "has", "b": [0]}], "overflow": False},
     "invalid_compact_relation_ref"),
    ({"e": [{"n": "Character", "b": [999]}], "p": [], "r": [], "overflow": False},
     "invalid_compact_evidence"),
    ({"e": [{"n": "Character", "b": [0]}],
      "p": [{"o": 1, "n": "Age", "t": "i", "b": [0]}], "r": [], "overflow": False},
     "invalid_compact_owner"),
    ({"e": [{"n": "Character", "b": [0]}],
      "p": [{"o": 0, "n": "Age", "t": "UNKNOWN", "b": [0]}], "r": [], "overflow": False},
     "invalid_compact_type"),
])
def test_invalid_results_fail_closed(payload, code):
    _, lookup = model_window(fixture_batch(), "RuneQuest")
    with pytest.raises(CompactContractError) as err:
        parse_discovery(payload, lookup, {"source_id": "source"})
    assert err.value.code == code


def test_ambiguous_reconciliation_is_operations_only_and_preserves_entity_references():
    proposals = [
        {"kind": "entity", "name": "Characters", "description": "", "evidence": [{"id": 1}]},
        {"kind": "entity", "name": "Character", "description": "", "evidence": [{"id": 2}]},
        {"kind": "property", "entity": "Characters", "name": "Strength",
         "data_type": "integer", "evidence": [{"id": 3}]},
        {"kind": "relationship", "source": "Characters", "target": "Character",
         "name": "KNOWS", "evidence": [{"id": 4}]},
    ]
    groups = ambiguous_groups(proposals)
    assert any({0, 1} <= set(group) for group in groups)
    packed = reconciliation_input(proposals, [0, 1])
    assert '"i":0' in packed and '"i":1' in packed
    assert "evidence" not in packed and "description" not in packed
    removed = apply_reconciliation(proposals, [0, 1], {"merge": [[0, 1]], "rename": [], "review": []})
    assert removed == {0}
    assert proposals[1]["evidence"] == [{"id": 2}, {"id": 1}]
    assert proposals[2]["entity"] == "Character"
    assert proposals[3]["source"] == "Character"
    found = {}
    for index, proposal in enumerate(proposals):
        if index not in removed:
            accumulate(found, proposal)
    assert len(found) == 3


def test_deterministic_conflict_detection_preserves_evidence():
    found = {}
    accumulate(found, {"kind": "property", "name": "Glory", "entity": "Character",
                       "data_type": "integer", "evidence": [{"block_ids": ["a"]}]})
    accumulate(found, {"kind": "property", "name": "Glory", "entity": "Character",
                       "data_type": "number", "evidence": [{"block_ids": ["b"]}]})
    item = next(iter(found.values()))
    assert item["conflict"]["reason"] == "inconsistent_source_property_type"
    assert len(item["evidence"]) == 2


def test_window_fingerprint_invalidates_contract_or_model_change():
    text, _ = model_window(fixture_batch(), "RuneQuest")
    key = cache_fingerprint(text, {"provider": "openrouter", "name": "model-a"})
    assert len(key) == 64
    assert key == cache_fingerprint(text, {"name": "model-a", "provider": "openrouter"})
    assert key != cache_fingerprint(text, {"provider": "openrouter", "name": "model-b"})
    assert key != cache_fingerprint(text + " ", {"provider": "openrouter", "name": "model-a"})


def test_token_aware_windows_are_non_lossy_and_smaller_than_old_batches():
    parsed = make_map()
    original = "Fighting with an armored shield. " * 1000
    blocks = {row.id: row for row in [
        block("b1", original, 1), block("b2", "Spear", 2),
        block("b3", "Dodge", 3), block("b4", "Glory", 4)]}
    batches = build_discovery_batches(parsed, blocks)
    assert len(batches) > 3
    assert MAX_INPUT_TOKENS <= 4000
    output = [x["text"] for batch in batches for section in batch["sections"]
              for x in section["blocks"] if x["id"] == "b1"]
    assert "".join(output) == original
    assert len({b["chapter_id"] for b in batches}) == 2
    assert all(len(model_window(batch, "Reference Source")[0].encode("utf-8")) < 14000
               for batch in batches)


def test_sqlite_window_checkpoint_persistence(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "compact.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = OntologyLearnRun(ontology_id=8, owner_id="owner", idempotency_key="key",
                               input_sources=[], base_revision=0, model_targets={})
        db.add(run)
        db.flush()
        window = OntologyLearnWindow(run_id=run.id, ontology_id=8, source_id="s1",
                                     document_id="d1", content_digest="a"*64,
                                     chapter_id="c", chapter_title="Combat", window_index=1,
                                     fingerprint="f"*64, contract_version=CONTRACT_VERSION,
                                     status="completed", proposals=[{"kind": "entity", "name": "Character"}],
                                     input_tokens=450, output_tokens=75)
        db.add(window)
        db.commit()
    with Session(engine) as db:
        rows = db.query(OntologyLearnWindow).all()
        assert len(rows) == 1
        assert rows[0].proposals[0]["name"] == "Character"
        assert rows[0].output_tokens == 75
    engine.dispose()


def test_truncation_is_detected_before_json_decode_and_is_not_retried():
    # A response that ends in a partial JSON string must fail with the precise
    # length reason, not a generic JSONDecodeError.
    response = SimpleNamespace(text='{"e":[{"n":"Unfinished', finish_reason="length")
    with pytest.raises(CompactContractError) as error:
        parse_model_response(response, stage="discovery")
    assert error.value.code == "discovery_output_truncated"
    with pytest.raises(CompactContractError) as malformed:
        parse_model_response(SimpleNamespace(text='{"e":', finish_reason="stop"), stage="discovery")
    assert malformed.value.code == "discovery_invalid_json"


def test_reconciliation_ops_reject_unrelated_merges_and_invalid_indices():
    proposals = [
        {"kind": "entity", "name": "Character", "evidence": []},
        {"kind": "property", "name": "Strength", "entity": "Character", "data_type": "integer", "evidence": []},
    ]
    with pytest.raises(CompactContractError) as error:
        apply_reconciliation(proposals, [0, 1], {"merge": [[1, 0]], "rename": [], "review": []})
    assert error.value.code == "invalid_reconciliation_merge"
    with pytest.raises(CompactContractError):
        apply_reconciliation(proposals, [0], {"merge": [], "rename": [[99, "X"]], "review": []})
