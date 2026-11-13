import json
from pathlib import Path

import pytest

from app.schemas.book_map import BookMapPageRange, BookMapV4
from app.services.book_map_contract import (
    UnsupportedBookMapVersion, project_v4_to_v3, read_book_map,
)
from shreckdoc.structure_validation import (
    StructureValidationError, descendants, stable_node_id, validate_book_map,
)


FIXTURES = Path(__file__).parent / "fixtures" / "bookmap_v4" / "contracts"


def _tree() -> BookMapV4:
    payload = json.loads((FIXTURES / "book_map_v4.json").read_text())
    root = payload["nodes"][0]
    chapter = {**root, "id": "chapter", "node_type": "chapter", "title": "Field Notes",
               "level": 1, "parent_id": "root", "position": {"physical_page": 1, "reading_order": 1},
               "pages": {"start": 1, "end": 3}, "page_locators": [
                   {"physical_page": 1, "printed_label": "i", "reading_order": 1},
                   {"physical_page": 3, "printed_label": "2", "reading_order": 8}],
               "child_ids": ["paragraph", "image"], "source_block_ids": ["heading-block"],
               "chunk_ids": ["chapter-chunk"], "resolution_status": "validated",
               "content_confidence": 0.4, "parent_confidence": 0.2,
               "structural_source": "bookmark"}
    paragraph = {**root, "id": "paragraph", "node_type": "paragraph", "title": None,
                 "level": 2, "parent_id": "chapter", "structural_node_id": "chapter",
                 "position": {"physical_page": 1, "reading_order": 2}, "pages": {"start": 1, "end": 1},
                 "page_locators": [{"physical_page": 1, "printed_label": "i", "reading_order": 2}],
                 "source_block_ids": ["body-block"], "chunk_ids": ["body-chunk"],
                 "resolution_status": "resolved"}
    image = {**paragraph, "id": "image", "node_type": "image", "level": 2,
             "position": {"physical_page": 2, "reading_order": 3}, "pages": {"start": 2, "end": 2},
             "page_locators": [{"physical_page": 2, "printed_label": "1", "reading_order": 3}],
             "source_block_ids": ["image-block"], "child_ids": [], "chunk_ids": [],
             "caption_node_ids": ["caption"], "resolution_status": "uncertain"}
    caption = {**paragraph, "id": "caption", "node_type": "caption", "level": 3,
               "parent_id": "image", "structural_node_id": "chapter",
               "position": {"physical_page": 2, "reading_order": 4}, "pages": {"start": 2, "end": 2},
               "page_locators": [{"physical_page": 2, "printed_label": "1", "reading_order": 4}],
               "source_block_ids": ["caption-block"], "chunk_ids": ["caption-chunk"],
               "resolution_status": "unresolved"}
    image["child_ids"] = ["caption"]
    root["child_ids"] = ["chapter"]
    payload["nodes"] = [root, chapter, paragraph, image, caption]
    payload["quality"] = {"node_count": 5, "source_block_count": 4, "owned_block_count": 4,
                          "unresolved_node_ids": ["caption"], "uncertain_node_ids": ["image"],
                          "validation_error_count": 0, "validation_warning_count": 0}
    return BookMapV4.model_validate(payload)


def test_valid_tree_traverses_in_canonical_order_and_has_stable_identity():
    tree = _tree()
    assert validate_book_map(tree) == []
    assert descendants(tree, "chapter") == ["paragraph", "image", "caption"]
    assert descendants(tree, "chapter", include_self=True)[0] == "chapter"
    assert stable_node_id("doc", "chapter", "bookmark:1") == stable_node_id("doc", "chapter", "bookmark:1")
    assert stable_node_id("doc", "chapter", "bookmark:1") != stable_node_id("doc", "chapter", "bookmark:2")


@pytest.mark.parametrize("mutate,expected", [
    (lambda tree: tree.model_copy(update={"root_id": "missing"}), "root_id must reference"),
    (lambda tree: tree.model_copy(update={"nodes": [*tree.nodes, tree.nodes[1]]}), "node IDs must be unique"),
    (lambda tree: tree.model_copy(update={"nodes": [tree.nodes[0], tree.nodes[1].model_copy(update={"source_block_ids": ["body-block"]}), *tree.nodes[2:]]}), "one owner"),
    (lambda tree: tree.model_copy(update={"nodes": [tree.nodes[0], tree.nodes[1].model_copy(update={"document_id": "other"}), *tree.nodes[2:]]}), "different document generation"),
    (lambda tree: tree.model_copy(update={"nodes": [tree.nodes[0].model_copy(update={"child_ids": ["chapter", "chapter"]}), *tree.nodes[1:]]}), "duplicate child IDs"),
    (lambda tree: tree.model_copy(update={"nodes": [tree.nodes[0].model_copy(update={"child_ids": []}), *tree.nodes[1:]]}), "reachable from the root"),
])
def test_tree_invariant_failures_are_reported(mutate, expected):
    assert any(expected in error for error in validate_book_map(mutate(_tree())))


def test_cycles_and_same_page_order_are_rejected():
    tree = _tree()
    chapter = tree.nodes[1].model_copy(update={"child_ids": ["image", "paragraph"]})
    reordered = tree.model_copy(update={"nodes": [tree.nodes[0], chapter, *tree.nodes[2:]]})
    assert any("canonical reading order" in error for error in validate_book_map(reordered))
    cyclic_image = tree.nodes[3].model_copy(update={"child_ids": ["chapter", "caption"]})
    cyclic = tree.model_copy(update={"nodes": [*tree.nodes[:3], cyclic_image, tree.nodes[4]]})
    assert any("cycle detected" in error for error in validate_book_map(cyclic))
    with pytest.raises(StructureValidationError, match="cycle detected"):
        descendants(cyclic, "root")


def test_page_ranges_cover_positions_and_all_locators():
    tree = _tree()
    chapter = tree.nodes[1].model_copy(update={"pages": BookMapPageRange(start=2, end=3)})
    invalid = tree.model_copy(update={"nodes": [tree.nodes[0], chapter, *tree.nodes[2:]]})
    assert any("position falls outside" in error for error in validate_book_map(invalid))


def test_parent_ranges_cover_descendants_and_parent_links_are_valid():
    tree = _tree()
    chapter = tree.nodes[1].model_copy(update={"pages": BookMapPageRange(start=1, end=1)})
    invalid_range = tree.model_copy(update={"nodes": [tree.nodes[0], chapter, *tree.nodes[2:]]})
    assert any("page range falls outside its parent" in error for error in validate_book_map(invalid_range))
    paragraph = tree.nodes[2].model_copy(update={"parent_id": "root", "level": 0})
    invalid_parent = tree.model_copy(update={"nodes": [tree.nodes[0], tree.nodes[1], paragraph, *tree.nodes[3:]]})
    assert any("not reciprocal" in error for error in validate_book_map(invalid_parent))
    assert any("greater than its parent" in error for error in validate_book_map(invalid_parent))


def test_version_reader_preserves_v3_and_projection_is_conservative():
    v3 = json.loads((FIXTURES / "book_map_v3.json").read_text())
    assert read_book_map(v3).map_version == 3
    with pytest.raises(UnsupportedBookMapVersion):
        read_book_map({"map_version": 5})
    projected = project_v4_to_v3(_tree())
    chapter, image = projected["sections"]
    assert projected["map_version"] == 3
    assert chapter["mapping_status"] == "verified" and chapter["confidence"] == 1.0
    assert chapter["confidence"] != 0.2  # Never expose parser confidence as V3 verification.
    assert chapter["block_ids"] == ["heading-block", "body-block", "image-block", "caption-block"]
    assert image["id"] == "image" and image["caption_block_ids"] == ["caption-block"]
    assert image["mapping_status"] == "tentative" and image["confidence"] == 0.25
