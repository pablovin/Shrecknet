"""RPG ontology schema-only V3 contract and checkpoint regression tests."""
from __future__ import annotations

from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.ontology_studio import OntologyLearnRun, OntologyLearnWindow
from app.services.ontology_compact import (
    CONTRACT_VERSION, DISCOVERY_SCHEMA, DISCOVERY_PROMPT, CompactContractError,
    model_window, cache_fingerprint, parse_discovery as parse_discovery_v4, proposal_counts,
    accumulate, ambiguous_groups, reconciliation_input, apply_reconciliation,
    parse_model_response,
)
from app.services.ontology_discovery_batches import MAX_INPUT_TOKENS, build_discovery_batches
from test_ontology_discovery_batches import make_map, block


def _v4_fixture(payload):
    """Upgrade legacy fixture shortcuts into the full strict V4 wire envelope."""
    result = dict(payload)
    if not isinstance(result.get("e"), list):
        return result
    result["e"] = []
    for entity in payload["e"]:
        row = dict(entity)
        row.setdefault("d", "")
        if isinstance(row.get("p"), list):
            row["p"] = []
            for prop in entity["p"]:
                item = dict(prop)
                item.setdefault("s", "")
                item.setdefault("d", "")
                item.setdefault("m", [])
                item.setdefault("i", [])
                row["p"].append(item)
        if isinstance(row.get("r"), list):
            row["r"] = [{**r, "d": r.get("d", "")} for r in entity["r"]]
        result["e"].append(row)
    return result


def parse_discovery(data, lookup, base, **kwargs):
    return parse_discovery_v4(_v4_fixture(data), lookup, base, **kwargs)


def sample_batch():
    return {
        "chapter_id": "chapter-3", "chapter_title": "Creating Investigators",
        "sections": [
            {"section_id": "s1", "heading_path": ["Investigator", "Characteristics"],
             "blocks": [{"id": "real-uuid-1", "text": "An investigator has age and Luck.",
                         "fragment_index": 0}]},
            {"section_id": "s2", "heading_path": ["Investigator", "Skills"],
             "blocks": [{"id": "real-uuid-2", "text": "An investigator has skills.",
                         "fragment_index": 0}]},
        ],
    }


def supported():
    return {"source_id": "book", "document_id": "doc",
            "content_digest": "a" * 64,
            "chapter_id": "chapter-3", "chapter_title": "Creating Investigators"}


def example_response():
    return {"e": [
        {"n": "Investigator", "b": [0],
         "p": [{"n": "age", "t": "i", "b": [0]}, {"n": "luck", "t": "i", "b": [0]}],
         "r": [{"n": "HAS_SKILL", "t": "Skill", "b": [1]}]},
        {"n": "Skill", "b": [1], "p": [], "r": []},
    ], "overflow": False}


def test_v3_is_nested_schema_only_with_multigame_grounding():
    assert CONTRACT_VERSION == "ontology-schema-v4"
    assert set(DISCOVERY_SCHEMA["properties"]) == {"e", "overflow"}
    shape = DISCOVERY_SCHEMA["properties"]["e"]["items"]
    assert set(shape["properties"]) == {"n", "d", "b", "p", "r"}
    assert "o" not in shape["properties"]["p"]["items"]["properties"]
    assert {"s", "d", "m", "i"} <= set(shape["properties"]["p"]["items"]["properties"])
    assert set(shape["properties"]["r"]["items"]["properties"]) == {"n", "t", "d", "b"}
    for game in ("Call of Cthulhu", "RuneQuest", "Pendragon", "D&D"):
        assert game in DISCOVERY_PROMPT
    for forbidden in ("instances", "catalog", "description", "source"):
        assert forbidden in DISCOVERY_PROMPT.lower()
    assert "WITHOUT SOURCE EVIDENCE" in DISCOVERY_PROMPT.upper()
    assert "Wizard and Fireball" in DISCOVERY_PROMPT


def test_nested_properties_have_implicit_owner_and_named_relationship_targets():
    prompt, lookup = model_window(sample_batch(), "Call of Cthulhu Core Rulebook")
    assert "real-uuid-1" not in prompt
    assert '"i":0' in prompt and '"i":1' in prompt
    counts = {}
    proposals = parse_discovery(example_response(), lookup, supported(), stats=counts)
    assert proposal_counts(proposals) == {"entity_types": 2, "properties": 2, "relationships": 1}
    assert [row["kind"] for row in proposals] == [
        "entity", "property", "property", "relationship", "entity"
    ]
    props = [p for p in proposals if p["kind"] == "property"]
    assert all(p["entity"] == "Investigator" for p in props)
    assert all(p["data_type"] == "integer" for p in props)
    assert all(p["value_schema"]["current_value"]["required"] for p in props)
    relationship = next(p for p in proposals if p["kind"] == "relationship")
    assert relationship["source"] == "Investigator"
    assert relationship["target"] == "Skill"
    assert relationship["evidence"][0]["block_ids"] == ["real-uuid-2"]
    assert props[0]["evidence"][0]["section_ids"] == ["s1"]
    assert counts["suppressed_instances"] == 0
    assert counts["unresolved_relationships"] == 0
    assert len(prompt) < 500


def test_schema_only_filters_obvious_named_instances_and_dependent_fields():
    input_data = {"e": [
        {"n": "Investigator", "b": [0], "p": [], "r": [
            {"n": "KNOWS", "t": "Cthulhu", "b": [1]}]},
        {"n": "Cthulhu", "b": [1], "p": [
            {"n": "hit_points", "t": "i", "b": [1]}], "r": []},
        {"n": "Spot Hidden", "b": [1], "p": [], "r": []},
        {"n": "Doctor of Medicine", "b": [1], "p": [], "r": []},
    ], "overflow": False}
    stats = {}
    _, lookup = model_window(sample_batch(), "Call of Cthulhu")
    result = parse_discovery(input_data, lookup, supported(), stats=stats)
    assert [row["name"] for row in result if row["kind"] == "entity"] == ["Investigator", "Spot Hidden"]
    assert stats["suppressed_instances"] == 2
    assert any(p["name"] == "Spot Hidden" and p["kind"] == "entity" for p in result)
    assert stats["unresolved_relationships"] == 1


def test_relationship_with_missing_target_is_counted_not_misassigned():
    input_data = {"e": [
        {"n": "Investigator", "b": [0], "p": [],
         "r": [{"n": "HAS_SKILL", "t": "Skill", "b": [1]}]}
    ], "overflow": False}
    _, lookup = model_window(sample_batch(), "Call of Cthulhu")
    stats = {}
    result = parse_discovery(input_data, lookup, supported(), stats=stats)
    assert proposal_counts(result)["relationships"] == 0
    assert stats["unresolved_relationships"] == 1
    input_data["e"].append({"n": "skill", "b": [1], "p": [], "r": []})
    result = parse_discovery(input_data, lookup, supported())
    assert [p["target"] for p in result if p["kind"] == "relationship"] == ["skill"]


@pytest.mark.parametrize("payload,code", [
    ({"e": [], "overflow": True}, "compact_output_overflow"),
    ({"e": [], "p": [], "r": [], "overflow": False}, "invalid_compact_envelope"),
    ({"e": [{"n": "Investigator", "b": [999], "p": [], "r": []}],
      "overflow": False}, "invalid_compact_evidence"),
    ({"e": [{"n": "Investigator", "b": [0], "p": [
        {"n": "age", "t": "BAD_TYPE", "b": [0]}], "r": []}],
      "overflow": False}, "invalid_compact_type"),
    ({"e": [{"n": "Investigator", "b": [0], "p": [
        {"o": 3, "n": "age", "t": "i", "b": [0]}], "r": []}],
      "overflow": False}, "invalid_compact_property"),
])
def test_invalid_responses_fail_closed_without_retries(payload, code):
    _, lookup = model_window(sample_batch(), "Call of Cthulhu")
    with pytest.raises(CompactContractError) as ex:
        parse_discovery(payload, lookup, supported())
    assert ex.value.code == code


def test_duplicate_type_names_merge_without_losing_source_evidence():
    _, lookup = model_window(sample_batch(), "Call of Cthulhu")
    raw = {"e": [
        {"n": "Investigator", "b": [0], "p": [{"n": "age", "t": "i", "b": [0]}], "r": []},
        {"n": "investigator", "b": [1], "p": [{"n": "age", "t": "i", "b": [1]}], "r": []},
    ], "overflow": False}
    proposals = parse_discovery(raw, lookup, supported())
    assert proposal_counts(proposals) == {"entity_types": 1, "properties": 1, "relationships": 0}
    assert [e["block_ids"] for e in proposals[0]["evidence"]] == [["real-uuid-1"], ["real-uuid-2"]]
    assert [e["block_ids"] for e in proposals[1]["evidence"]] == [["real-uuid-1"], ["real-uuid-2"]]


def test_reconciliation_merges_schema_types_without_dropping_references():
    proposals = [
        {"kind": "entity", "name": "Characters", "evidence": [{"id": 1}]},
        {"kind": "entity", "name": "Character", "evidence": [{"id": 2}]},
        {"kind": "property", "entity": "Characters", "name": "age",
         "data_type": "integer", "evidence": [{"id": 3}]},
        {"kind": "relationship", "source": "Characters", "target": "Character",
         "name": "KNOWS", "evidence": [{"id": 4}]},
    ]
    groups = ambiguous_groups(proposals)
    assert any({0, 1} <= set(group) for group in groups)
    payload = reconciliation_input(proposals, [0, 1])
    assert "evidence" not in payload
    removed = apply_reconciliation(proposals, [0, 1],
                                   {"merge": [[0, 1]], "rename": [], "review": []})
    assert removed == {0}
    assert proposals[2]["entity"] == "Character"
    assert proposals[3]["source"] == "Character"
    merged = {}
    for i, proposal in enumerate(proposals):
        if i not in removed:
            accumulate(merged, proposal)
    assert proposal_counts(merged.values()) == {
        "entity_types": 1, "properties": 1, "relationships": 1,
    }


def test_conflicts_preserve_evidence():
    items = {}
    accumulate(items, {"kind": "property", "name": "Glory", "entity": "Knight",
                       "data_type": "integer", "evidence": [{"block_ids": ["one"]}]})
    accumulate(items, {"kind": "property", "name": "Glory", "entity": "Knight",
                       "data_type": "number", "evidence": [{"block_ids": ["two"]}]})
    item = next(iter(items.values()))
    assert item["conflict"]["reason"] == "inconsistent_source_property_type"
    assert len(item["evidence"]) == 2


def test_v3_fingerprint_invalidates_v2_cache():
    prompt, _ = model_window(sample_batch(), "Call of Cthulhu")
    key = cache_fingerprint(prompt, {"provider": "openrouter", "name": "model-1"})
    assert len(key) == 64
    assert key != cache_fingerprint(prompt + " ", {"provider": "openrouter", "name": "model-1"})
    assert key != cache_fingerprint(prompt, {"provider": "openrouter", "name": "model-2"})


def test_token_bounded_windows_retain_all_explanatory_text():
    original = "Playing a knight involves chivalry. " * 1000
    b = {row.id: row for row in [
        block("b1", original, 1), block("b2", "A Passion is a trait", 2),
        block("b3", "Character", 3), block("b4", "Glory", 4)]}
    batches = build_discovery_batches(make_map(), b)
    assert MAX_INPUT_TOKENS <= 4000
    assert len(batches) > 3
    pieces = [frag["text"] for batch in batches for section in batch["sections"]
              for frag in section["blocks"] if frag["id"] == "b1"]
    assert "".join(pieces) == original


def test_sqlite_window_uses_v3_version(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "v3.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = OntologyLearnRun(ontology_id=4, owner_id="owner", idempotency_key="k",
                               input_sources=[], base_revision=0, model_targets={})
        db.add(run)
        db.flush()
        db.add(OntologyLearnWindow(
            run_id=run.id, ontology_id=4, source_id="source", document_id="doc",
            content_digest="a"*64, chapter_id="intro", chapter_title="Intro",
            window_index=1, fingerprint="f"*64, contract_version=CONTRACT_VERSION,
            status="completed", proposals=[{"kind": "entity", "name": "Knight"}],
            input_tokens=700, output_tokens=150))
        db.commit()
    with Session(engine) as db:
        item = db.query(OntologyLearnWindow).one()
        assert item.contract_version == "ontology-schema-v4"
        assert item.output_tokens == 150
    engine.dispose()


def test_finish_reason_detects_truncation_before_json_parse():
    with pytest.raises(CompactContractError) as error:
        parse_model_response(
            SimpleNamespace(text='{"e":[{"n":"Truncated', finish_reason="length"),
            stage="discovery")
    assert error.value.code == "discovery_output_truncated"
    with pytest.raises(CompactContractError):
        parse_model_response(
            SimpleNamespace(text='{"e":', finish_reason="stop"),
            stage="discovery")


def test_near_approved_names_can_be_reconciled_without_full_descriptions():
    candidates = [{"kind": "entity", "name": "Character Statistics", "evidence": []}]
    known = [{"kind": "entity", "name": "Character Statistic",
              "description": "MUST NOT BE SENT"}]
    groups = ambiguous_groups(candidates, known=known)
    assert groups == [[0]]
    prompt = reconciliation_input(candidates, groups[0], known=known)
    assert "Character Statistic" in prompt
    assert "MUST NOT BE SENT" not in prompt
