"""Regression checks for chapter-aware combined Architect ontology extraction."""
from __future__ import annotations
from types import SimpleNamespace

from app.schemas.shreckworld import BookMapRead
from app.services.ontology_discovery_batches import build_discovery_batches, MAX_BATCH_CHARS


def make_map():
    data = {
        "document_id": "doc", "map_version": 3,
        "sections": [
            {"id": "c1", "title": "Combat", "node_type": "chapter", "level": 1,
             "parent_id": None, "heading_path": ["Combat"], "source_block_ids": ["b1", "b2", "b3"]},
            {"id": "s1", "title": "Attack", "node_type": "section", "level": 2,
             "parent_id": "c1", "heading_path": ["Combat", "Attack"], "source_block_ids": ["b1", "b2"]},
            {"id": "s2", "title": "Defense", "node_type": "section", "level": 2,
             "parent_id": "c1", "heading_path": ["Combat", "Defense"], "source_block_ids": ["b3"]},
            {"id": "c2", "title": "Character", "node_type": "chapter", "level": 1,
             "parent_id": None, "heading_path": ["Character"], "source_block_ids": ["b4"]},
        ],
        "unmapped_block_ids": ["extra"],
        "quality": {
            "section_count": 4, "block_count": 5, "mapped_block_count": 4,
            "coverage": 0.8, "unmapped_block_count": 1, "uncertain_section_ids": [],
        },
    }
    for section in data["sections"]:
        section["confidence"] = 1.0
        section["mapping_status"] = "verified"
        section["pages"] = {"start": 1, "end": 1}
        section["block_ids"] = section["source_block_ids"]
        section["caption_block_ids"] = []
        section["chunk_ids"] = []
        section["content_types"] = ["paragraph"]
    return BookMapRead.model_validate(data)


def block(id, text, order):
    return SimpleNamespace(id=id, display_text=text, reading_order=order,
                           provenance=[], block_type="paragraph")


def test_short_subsections_in_same_chapter_share_one_model_request():
    parsed = make_map()
    blocks = {row.id: row for row in [
        block("b1", "Sword", 1), block("b2", "Shield", 2),
        block("b3", "Evade", 3), block("b4", "Glory", 4),
    ]}
    batches = build_discovery_batches(parsed, blocks)
    assert len(batches) == 2
    assert batches[0]["chapter_title"] == "Combat"
    assert [section["heading_path"][-1] for section in batches[0]["sections"]] == ["Attack", "Defense"]
    ids = [part["id"] for batch in batches for section in batch["sections"] for part in section["blocks"]]
    assert ids == ["b1", "b2", "b3", "b4"]
    assert batches[1]["chapter_title"] == "Character"


def test_token_bounded_window_fragmentation_preserves_text_and_chapter_ownership():
    parsed = make_map()
    original = "Proficiency " * 80
    blocks = {row.id: row for row in [
        block("b1", original, 1),
        block("b2", "Shield", 2),
        block("b3", "Evade", 3),
        block("b4", "Glory", 4),
    ]}
    batches = build_discovery_batches(parsed, blocks, max_chars=300)
    assert len(batches) > 2
    assert all(sum(len(part["text"]) for section in batch["sections"] for part in section["blocks"]) <= 300
               for batch in batches)
    assert [batch["chapter_title"] for batch in batches].count("Character") == 1
    assert all(batch["chapter_title"] in {"Combat", "Character"} for batch in batches)
    pieces = [part["text"] for batch in batches for section in batch["sections"] for part in section["blocks"]
              if part["id"] == "b1"]
    assert "".join(pieces) == original
    assert sorted(part["fragment_index"] for batch in batches for section in batch["sections"]
                  for part in section["blocks"] if part["id"] == "b1") == list(range(len(pieces)))


def test_missing_bookmap_assignment_is_preserved_as_unmapped():
    parsed = make_map()
    blocks = {row.id: row for row in [
        block("b1", "Sword", 1), block("extra", "Unmapped appendix", 5),
    ]}
    batches = build_discovery_batches(parsed, blocks)
    assert len(batches) == 2
    assert batches[1]["chapter_id"] == "unmapped"
    assert batches[1]["sections"][0]["blocks"][0]["text"] == "Unmapped appendix"


def test_rejects_unsafe_character_budget():
    import pytest
    with pytest.raises(ValueError, match="max_chars"):
        build_discovery_batches(make_map(), {}, max_chars=50)


def test_default_window_limits_are_bounded():
    assert 4000 <= MAX_BATCH_CHARS <= 10000
