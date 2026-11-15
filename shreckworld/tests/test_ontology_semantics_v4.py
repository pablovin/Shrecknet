"""World-scoped V4 semantic properties, instance schemas and source evidence."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.services import ontology_studio as studio
from app.services.ontology_compact import (
    CONTRACT_VERSION, CompactContractError, parse_discovery, accumulate,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "semantics.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_properties_are_world_local_and_instantiate_current_value(db):
    coc = studio.create_entity(db, 7, {"name": "Investigator"}, 0, "builder")
    rq = studio.create_entity(db, 8, {"name": "Adventurer"}, 0, "builder")
    investigator = coc["entities"][0]["id"]
    adventurer = rq["entities"][0]["id"]
    coc = studio.create_property(db, 7, investigator, {
        "name": "strength", "data_type": "integer", "semantic_type": "Characteristic",
        "description": "Physical strength",
        "definition": {"abbreviation": "STR"},
    }, 1, "builder")
    rq = studio.create_property(db, 8, adventurer, {
        "name": "strength", "data_type": "integer", "semantic_type": "Characteristic",
        "definition": {"system": "RuneQuest"},
    }, 1, "builder")
    template = studio.instantiate_entity_properties(coc, investigator)
    assert template == {"strength": {"current_value": None}}
    prop = coc["properties"][0]
    assert prop["semantic_type"] == "Characteristic"
    assert prop["value_schema"]["current_value"] == {
        "type": "integer", "required": True, "nullable": True,
    }
    assert rq["properties"][0]["definition"] == {"system": "RuneQuest"}
    assert prop["id"] != rq["properties"][0]["id"]
    with pytest.raises(HTTPException):
        studio.instantiate_entity_properties(coc, adventurer)


def test_instance_template_inherits_and_supports_extra_skill_state(db):
    base = studio.create_entity(db, 7, {"name": "Actor"}, 0, "builder")
    actor_id = base["entities"][0]["id"]
    sub = studio.create_entity(db, 7, {
        "name": "Investigator", "parent_id": actor_id,
    }, 1, "builder")
    investigator_id = next(e["id"] for e in sub["entities"] if e["name"] == "Investigator")
    graph = studio.create_property(db, 7, actor_id, {
        "name": "spot_hidden", "data_type": "integer", "semantic_type": "Skill",
        "definition": {"base_chance": 25},
        "value_schema": {
            "current_value": {"type": "integer", "required": True, "nullable": True},
            "improvement_mark": {"type": "boolean", "required": False, "nullable": True},
        },
    }, 2, "builder")
    assert studio.instantiate_entity_properties(graph, investigator_id) == {
        "spot_hidden": {"current_value": None, "improvement_mark": None}
    }
    with pytest.raises(HTTPException) as error:
        studio.update_property(db, 7, graph["properties"][0]["id"], {
            "data_type": "text"
        }, 3, "builder")
    assert error.value.status_code == 422


def test_v4_source_evidence_and_semantic_fields():
    assert CONTRACT_VERSION == "ontology-schema-v4"
    lookup = {0: {"block_id": "b1", "section_id": "s1", "fragment_index": 0}}
    base = {"source_id": "source", "document_id": "doc", "chapter_id": "chapter",
            "chapter_title": "Skills", "content_digest": "digest"}
    content = {"e": [{
        "n": "Investigator", "d": "Playable character", "b": [0],
        "p": [{
            "n": "spot_hidden", "t": "i", "s": "Skill", "d": "Spot clues",
            "m": [{"n": "base_chance", "t": "i", "v": "25"}],
            "i": [{"n": "current_value", "t": "i", "r": True},
                  {"n": "improvement_mark", "t": "b", "r": False}], "b": [0],
        }],
        "r": [],
    }], "overflow": False}
    output = parse_discovery(content, lookup, base)
    prop = next(p for p in output if p["kind"] == "property")
    assert prop["definition"] == {"base_chance": 25}
    assert prop["semantic_type"] == "Skill"
    assert prop["value_schema"]["current_value"]["required"]
    assert prop["evidence"][0]["block_ids"] == ["b1"]
    found = {}
    accumulate(found, prop)
    accumulate(found, {**prop, "evidence": [{**prop["evidence"][0], "block_ids": ["b2"]}]})
    assert len(next(iter(found.values()))["evidence"]) == 2
    content["e"][0]["p"][0]["i"][0]["t"] = "s"
    with pytest.raises(CompactContractError) as exc:
        parse_discovery(content, lookup, base)
    assert exc.value.code == "invalid_current_value_type"



@pytest.mark.parametrize("declared,raw,expected", [
    ("i", "25%", "25%"),
    ("i", "1D6+2", "1D6+2"),
    ("i", "varies", "varies"),
    ("f", "NaN", "NaN"),
    ("b", "sometimes", "sometimes"),
])
def test_unparseable_metadata_is_reviewable_not_a_failed_window(declared, raw, expected):
    from app.services.ontology_compact import _metadata
    warnings = []
    metadata = _metadata([{"n": "base_chance", "t": declared, "v": raw}],
                         warnings=warnings)
    assert metadata == {"base_chance": expected}
    assert warnings == [{
        "code": "metadata_value_unparsed",
        "field": "base_chance",
        "declared_type": {"i": "integer", "f": "number", "b": "boolean"}[declared],
        "raw_value": raw,
    }]


def test_metadata_unparseable_field_cannot_be_auto_approved_and_survives_merge():
    lookup = {0: {"block_id": "b1", "section_id": "s1", "fragment_index": 0}}
    base = {"source_id": "book", "document_id": "doc", "chapter_id": "4",
            "chapter_title": "Skills", "content_digest": "digest"}
    response = {"e": [{
        "n": "Investigator", "d": "Player character", "b": [0],
        "p": [{
            "n": "dodge", "t": "i", "s": "Skill", "d": "Avoid danger",
            "m": [
                {"n": "base_chance", "t": "i", "v": "DEX/2"},
                {"n": "skill_category", "t": "s", "v": "Combat"},
            ],
            "i": [{"n": "current_value", "t": "i", "r": True}], "b": [0],
        }], "r": [],
    }], "overflow": False}
    stats = {}
    candidates = parse_discovery(response, lookup, base, stats=stats)
    prop = next(x for x in candidates if x["kind"] == "property")
    assert prop["definition"] == {"base_chance": "DEX/2", "skill_category": "Combat"}
    assert prop["conflict"]["reason"] == "metadata_requires_review"
    assert prop["conflict"]["fields"] == ["base_chance"]
    assert prop["validation_warnings"][0]["raw_value"] == "DEX/2"
    assert prop["evidence"][0]["block_ids"] == ["b1"]
    assert stats["metadata_warnings"] == 1
    found = {}
    accumulate(found, prop)
    accumulate(found, {**prop, "definition": {"base_chance": "DEX/2"},
                       "validation_warnings": [], "conflict": None,
                       "evidence": [{**prop["evidence"][0], "block_ids": ["b2"]}]})
    stored = next(iter(found.values()))
    assert stored["conflict"]["reason"] == "metadata_requires_review"
    assert len(stored["evidence"]) == 2


def test_boolean_metadata_normalizes_common_unambiguous_literal_values():
    from app.services.ontology_compact import _metadata
    for literal, result in [("true", True), ("FALSE", False),
                            ("yes", True), ("no", False), ("1", True), ("0", False)]:
        warnings = []
        parsed = _metadata([{"n": "available", "t": "b", "v": literal}], warnings=warnings)
        assert parsed["available"] is result
        assert not warnings


def test_incompatible_metadata_envelope_remains_strict():
    from app.services.ontology_compact import _metadata
    with pytest.raises(CompactContractError) as error:
        _metadata([{"n": "chance", "t": "i", "v": {"untrusted": "object"}}], warnings=[])
    assert error.value.code == "invalid_compact_metadata"


def test_unparseable_metadata_is_retained_as_text_without_losing_valid_values():
    lookup = {0: {"block_id": "b1", "section_id": "s1", "fragment_index": 0}}
    base = {"source_id": "source", "document_id": "doc", "chapter_id": "chapter",
            "chapter_title": "Skills", "content_digest": "digest"}
    content = {"e": [{
        "n": "Investigator", "d": "Playable character", "b": [0], "r": [],
        "p": [{
            "n": "spot_hidden", "t": "i", "s": "Skill", "d": "",
            "m": [
                {"n": "base_chance", "t": "i", "v": "25"},
                {"n": "specialization", "t": "i", "v": "varies by edition"},
                {"n": "maximum", "t": "f", "v": 99},
            ],
            "i": [], "b": [0],
        }],
    }], "overflow": False}

    output = parse_discovery(content, lookup, base)
    prop = next(p for p in output if p["kind"] == "property")
    assert prop["definition"] == {
        "base_chance": 25,
        "specialization": "varies by edition",
        "maximum": 99.0,
    }


def test_native_boolean_metadata_and_reviewed_type_mismatch():
    from app.services.ontology_compact import _metadata
    warnings = []
    value = _metadata([
        {"n": "enabled", "t": "b", "v": True},
        {"n": "numeric", "t": "i", "v": 15},
        {"n": "invalid_integer", "t": "i", "v": False},
    ], warnings=warnings)
    assert value["enabled"] is True
    assert value["numeric"] == 15
    assert value["invalid_integer"] == "False"
    assert len(warnings) == 1
    assert warnings[0]["field"] == "invalid_integer"
