import pytest
from shreckdoc.pipeline import NormalizedBlock, NormalizedDocument, NormalizedPage
from shreckdoc.structure_headings import HeadingCandidate, HeadingInferenceResult
from shreckdoc.structure_resolution import resolve_structure
from shreckdoc.structure_content import project_resolved_document
from app.services.book_map_v4 import build_book_map_v4
from app.services.book_map_content import read_node_content
from app.schemas.book_map import BookMapChildSummary, BookMapContentItem
from pydantic import ValidationError


def _block(block_id, kind, text, page, order, *, reference=None, parent_reference=None, metadata=None):
    return NormalizedBlock(
        block_id, "provisional", kind, text, [page], [str(page)],
        [{"left": 10.0, "top": float(order * 20), "right": 300.0, "bottom": float(order * 20 + 10)}],
        order, reference or block_id,
        {"provenance": [{"page_no": page}], "parent_reference": parent_reference,
         "native": metadata or {}},
    )


def _fixture():
    pages = [NormalizedPage(f"p{page}", page, str(page), width=600, height=800) for page in range(1, 5)]
    blocks = [
        _block("h1", "heading", "Chapter One", 1, 0, reference="heading-ref"),
        _block("p1", "paragraph", "Opening text", 1, 1),
        _block("t1", "table", "A | B", 2, 2, reference="table-ref"),
        _block("i1", "picture", "", 2, 3, reference="image-ref"),
        _block("c1", "caption", "Compass rose", 2, 4, parent_reference="image-ref",
               metadata={"parent_reference": "image-ref"}),
        _block("u1", "unknown", "Retained unknown evidence", 3, 5),
    ]
    document = NormalizedDocument(pages, [], blocks)
    headings = HeadingInferenceResult([HeadingCandidate("Chapter One", "h1", 1, 0, 1, 0.9,
                                                        "candidate", ["explicit_heading"], None)], [], True)
    resolution = resolve_structure(document_id="doc-v4", document=document, heading_result=headings)
    return document, resolution


def test_v4_content_tree_owns_blocks_once_and_links_caption_without_duplicate_ownership():
    document, resolution = _fixture()
    tree = build_book_map_v4(document_id="doc-v4", source_sha256="a" * 64,
                             document=document, resolution=resolution)
    nodes = {node.id: node for node in tree.nodes}
    by_type = {node.node_type: node for node in tree.nodes if node.node_type in {"chapter", "paragraph", "table", "image", "caption", "unknown"}}
    assert tree.map_version == 4 and tree.quality.source_block_count == 6
    assert tree.quality.owned_block_count == 6
    assert len({block for node in tree.nodes for block in node.source_block_ids}) == 6
    assert by_type["chapter"].source_block_ids == ["h1"]
    assert by_type["image"].source_block_ids == ["i1"]
    assert by_type["image"].caption_node_ids == [by_type["caption"].id]
    assert by_type["caption"].source_block_ids == ["c1"]
    assert by_type["unknown"].source_block_ids == ["u1"]
    assert any(node.node_type == "unparsed_region" and node.position.physical_page == 4 for node in nodes.values())
    assert document.pages[-1].accounting.coverage == "unknown"


def test_page_104_content_is_owned_by_chapter_two_and_keeps_page_ranges():
    document, _ = _fixture()
    document.pages.extend([NormalizedPage("p104", 104, "104", width=600, height=800)])
    document.blocks.append(_block("h2", "heading", "Chapter Two", 100, 8, reference="heading2"))
    document.blocks.append(_block("body104", "paragraph", "Interior text", 104, 9))
    headings = HeadingInferenceResult([
        HeadingCandidate("Chapter One", "h1", 1, 0, 1, 0.9, "candidate", [], None),
        HeadingCandidate("Chapter Two", "h2", 100, 8, 1, 0.9, "candidate", [], None),
    ], [], True)
    resolution = resolve_structure(document_id="doc-v4", document=document, heading_result=headings)
    tree = build_book_map_v4(document_id="doc-v4", source_sha256="b" * 64,
                             document=document, resolution=resolution)
    blocks = {block.block_id: block for block in document.blocks}
    body_node = next(node for node in tree.nodes if node.source_block_ids == ["body104"])
    assert body_node.parent_id == next(node.id for node in tree.nodes if node.title == "Chapter Two")
    assert body_node.pages.start == 104


def test_resolved_projection_keeps_rejected_heading_text_as_paragraph_content():
    document, _ = _fixture()
    document.blocks.append(_block("rejected", "heading", "Possible but rejected", 3, 6))
    headings = HeadingInferenceResult([HeadingCandidate("Chapter One", "h1", 1, 0, 1, 0.9,
                                                        "candidate", [], None)], [], True)
    resolution = resolve_structure(document_id="doc-v4", document=document, heading_result=headings)
    projected = project_resolved_document(document, resolution)
    rejected = next(block for block in projected.blocks if block.block_id == "rejected")
    assert rejected.block_type == "paragraph" and rejected.display_text == "Possible but rejected"
    assert "rejected" in next(section for section in projected.sections if section.section_id == rejected.section_id).block_ids
    assert document.blocks[-1].block_type == "heading"


def test_large_content_tree_keeps_block_ownership_complete():
    pages = [NormalizedPage(f"p{i}", i, str(i)) for i in range(1, 301)]
    blocks = [_block(f"large-{i}", "paragraph", f"Evidence {i}", i, i) for i in range(1, 301)]
    document = NormalizedDocument(pages, [], blocks)
    resolution = resolve_structure(document_id="large-doc", document=document)
    tree = build_book_map_v4(document_id="large-doc", source_sha256="c" * 64,
                             document=document, resolution=resolution)
    assert tree.quality.source_block_count == tree.quality.owned_block_count
    assert len(tree.nodes) > tree.quality.source_block_count


def test_node_content_contract_requires_source_positions_for_interleaving():
    position = {"physical_page": 12, "reading_order": 4}
    item = BookMapContentItem(block_id="b1", content_type="paragraph", page_numbers=[12],
        position=position, bounding_boxes=[], media_ref=None, text="Source text")
    child = BookMapChildSummary(id="n2", node_type="section", title="Details",
        pages={"start": 12, "end": 13}, position={"physical_page": 12, "reading_order": 5})
    assert item.position.physical_page == child.position.physical_page == 12
    assert item.position.reading_order < child.position.reading_order
    with pytest.raises(ValidationError):
        BookMapContentItem(block_id="b1", content_type="paragraph", page_numbers=[12],
            bounding_boxes=[], media_ref=None, text="Source text")


def test_node_content_response_positions_direct_blocks_and_child_summaries():
    document, resolution = _fixture()
    tree = build_book_map_v4(document_id="doc-v4", source_sha256="d" * 64,
                             document=document, resolution=resolution)
    chapter = next(node for node in tree.nodes if node.node_type == "chapter")
    block = type("Block", (), {"id": "h1", "block_type": "heading", "display_text": "Chapter One",
        "reading_order": 0, "provenance": [{"page_no": 1}], "block_metadata": {}})()

    class Session:
        def get(self, *_args): return type("MapRecord", (), {"payload": tree.model_dump(mode="json")})()
        def scalars(self, *_args): return [block]

    response = read_node_content(Session(), document=type("Document", (), {
        "id": "doc-v4", "source_sha256": "d" * 64})(), node_id=chapter.id)
    assert response["items"][0]["position"] == {"physical_page": 1, "reading_order": 0}
    child = next(node for node in tree.nodes if node.id == chapter.child_ids[0])
    assert response["children"][0]["position"] == child.position.model_dump()

