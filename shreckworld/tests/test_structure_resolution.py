from shreckdoc.pipeline import NormalizedBlock, NormalizedDocument, NormalizedPage
from shreckdoc.structure_headings import HeadingCandidate, HeadingInferenceResult
from shreckdoc.structure_resolution import resolve_structure
from shreckdoc.structure_toc import TocCandidate
from app.services.pdf_structure_evidence import PdfOutlineEvidence


def _document():
    pages = [NormalizedPage(f"p{page}", page, str(page)) for page in (10, 50, 100, 104, 120)]
    blocks = [NormalizedBlock(f"b{i}", "root", "heading", text, [page], [],
                              [{"left": 10.0, "top": y, "right": 300.0, "bottom": y + 10}], order,
                              f"ref-{i}")
              for i, (text, page, y, order) in enumerate([
                  ("Chapter One", 10, 10.0, 0), ("Opening", 10, 40.0, 1),
                  ("Later section", 50, 30.0, 2), ("Chapter Two", 100, 10.0, 3),
                  ("Interior heading", 104, 45.0, 4), ("Chapter Two", 120, 5.0, 5),
              ])]
    return NormalizedDocument(pages, [], blocks)


def _heading(block_id, title, page, order, level=1, confidence=0.8):
    return HeadingCandidate(title, block_id, page, order, level, confidence, "candidate", [], None)


def _bookmark(reference, title, page, level, order, y=None, diagnostics=None):
    return PdfOutlineEvidence(f"bm-{reference}", title, level, [order], order, page,
                              None, y, "1", None, reference, diagnostics or [])


def _by_title(result):
    return {node.title: node for node in result.nodes}


def test_chapter_bookmarks_exclude_parser_subsections_and_page_104_stays_in_chapter_two():
    document = _document()
    headings = HeadingInferenceResult([
        _heading("b0", "Chapter One", 10, 0), _heading("b1", "Opening", 10, 1, 2),
        _heading("b2", "Later section", 50, 2, 2), _heading("b3", "Chapter Two", 100, 3),
        _heading("b4", "Interior heading", 104, 4, 2),
    ], [], True)
    bookmarks = [_bookmark("one", "Chapter One", 10, 1, 0), _bookmark("two", "Chapter Two", 100, 1, 1)]
    result = resolve_structure(document_id="doc", document=document, bookmarks=bookmarks, heading_result=headings)
    by_title = _by_title(result)
    assert by_title["Chapter One"].parent_id == result.nodes[0].node_id
    assert "Opening" not in by_title and "Interior heading" not in by_title
    assert len(result.nodes) == 3
    assert by_title["Chapter One"].end_exclusive.physical_page == 100


def test_unusable_outline_and_partial_toc_allow_heading_candidates():
    document = _document()
    headings = HeadingInferenceResult([_heading("b0", "Chapter One", 10, 0),
                                       _heading("b2", "Later section", 50, 2, 2)], [], True)
    toc = TocCandidate("Chapter One", "1", 2, 0, "toc-block", 0, evidence_id="toc-one",
                       mapped_physical_page=10, mapping_status="mapped", mapping_reason="unique_title_anchor")
    result = resolve_structure(document_id="doc", document=document,
                               bookmarks=[_bookmark("bad", "Foreign", 900, 1, 0)],
                               toc_candidates=[toc], heading_result=headings)
    by_title = _by_title(result)
    assert "Chapter One" in by_title and "Later section" in by_title
    assert "invalid_bookmark_not_used" in result.diagnostics
    assert by_title["Chapter One"].source_kind == "toc"


def test_bookmark_priority_merges_same_heading_evidence_and_conflicts_remain_uncertain():
    document = _document()
    headings = HeadingInferenceResult([_heading("b0", "Chapter One", 10, 0)], [], True)
    result = resolve_structure(document_id="doc", document=document,
                               bookmarks=[_bookmark("top", "Chapter One", 10, 1, 0, y=10)],
                               heading_result=headings)
    chapter = result.nodes[1]
    assert chapter.source_kind == "bookmark" and len(chapter.evidence_ids) == 2

    conflict = HeadingInferenceResult([_heading("b0", "Chapter One", 10, 0),
                                       _heading("b1", "Different title", 10, 0)], [], True)
    document.blocks[1].bounding_boxes[0]["top"] = 10
    contested = resolve_structure(document_id="doc", document=document, heading_result=conflict)
    assert all(node.resolution_status == "uncertain" for node in contested.nodes[1:])
    assert contested.conflicts


def test_duplicate_titles_at_distinct_locations_and_same_page_chapter_boundaries_are_stable():
    document = _document()
    bookmarks = [_bookmark("first", "Chapter Two", 100, 1, 0), _bookmark("second", "Chapter Two", 120, 1, 1)]
    first = resolve_structure(document_id="doc", document=document, bookmarks=bookmarks)
    second = resolve_structure(document_id="doc", document=document, bookmarks=reversed(bookmarks))
    chapters = [node for node in first.nodes if node.title == "Chapter Two"]
    assert len(chapters) == 2 and chapters[0].node_id != chapters[1].node_id
    assert [node.node_id for node in first.nodes] == [node.node_id for node in second.nodes]

    same_page = [_bookmark("a", "Chapter Alpha", 10, 1, 0), _bookmark("b", "Chapter Beta", 10, 1, 1)]
    boundaries = resolve_structure(document_id="doc", document=document, bookmarks=same_page)
    assert boundaries.nodes[1].position.reading_order < boundaries.nodes[2].position.reading_order
    assert boundaries.nodes[1].end_exclusive.reading_order == boundaries.nodes[2].position.reading_order


def test_no_evidence_returns_safe_root_only_tree():
    result = resolve_structure(document_id="doc", document=NormalizedDocument([], [], []))
    assert len(result.nodes) == 1 and result.nodes[0].node_type == "document"
    assert not result.sufficient_evidence
    assert "no_structural_candidates_root_only" in result.diagnostics


def test_internal_bookmark_toc_and_heading_evidence_matrix_preserves_fallbacks():
    document = _document()
    headings = HeadingInferenceResult([_heading("b0", "Chapter One", 10, 0),
                                       _heading("b2", "Later section", 50, 2, 2)], [], True)
    bookmarks = [_bookmark("one", "Chapter One", 10, 1, 0)]
    toc = [TocCandidate("Later section", "iv", 1, 1, "contents", 0,
        evidence_id="toc-later", mapped_physical_page=50, mapping_status="mapped")]
    full = resolve_structure(document_id="matrix", document=document, bookmarks=bookmarks,
                             toc_candidates=toc, heading_result=headings)
    no_bookmarks = resolve_structure(document_id="matrix", document=document, bookmarks=bookmarks,
        toc_candidates=toc, heading_result=headings, ignore_bookmarks=True)
    headings_only = resolve_structure(document_id="matrix", document=document, bookmarks=bookmarks,
        toc_candidates=toc, heading_result=headings, ignore_bookmarks=True, ignore_toc=True)
    assert any(node.source_kind == "bookmark" for node in full.nodes[1:])
    assert all(node.source_kind != "bookmark" for node in no_bookmarks.nodes[1:])
    assert any(node.source_kind == "toc" for node in no_bookmarks.nodes[1:])
    assert all(node.source_kind == "heading" for node in headings_only.nodes[1:])


def test_bookmark_intervals_reassign_unmatched_headings_and_tables():
    from shreckdoc.structure_content import attach_content, project_resolved_document
    document = _document()
    document.blocks[2].block_type = "table"
    headings = HeadingInferenceResult([_heading("b1", "Opening", 10, 1, 2),
                                       _heading("b4", "Interior heading", 104, 4, 2)], [], True)
    result = resolve_structure(document_id="doc", document=document,
        bookmarks=[_bookmark("one", "Chapter One", 10, 1, 0),
                   _bookmark("two", "Chapter Two", 100, 1, 1)], heading_result=headings)
    first, second = result.nodes[1:]
    owners = attach_content(document, result).block_owner_ids
    assert owners["b1"] == first.node_id
    assert owners["b2"] == first.node_id
    assert owners["b4"] == second.node_id
    projected = project_resolved_document(document, result)
    assert {block.block_id: block.section_id for block in projected.blocks} == owners
    assert next(block for block in projected.blocks if block.block_id == "b4").block_type == "paragraph"


def test_printed_contents_is_one_top_level_component():
    document = _document()
    toc = [TocCandidate("Chapter One", "1", 1, 1, "b0", 0,
        evidence_id="toc-one", mapped_physical_page=10, mapping_status="mapped")]
    result = resolve_structure(document_id="doc", document=document,
        bookmarks=[_bookmark("one", "Chapter One", 10, 1, 0)], toc_candidates=toc)
    contents = [node for node in result.nodes if node.node_type == "contents"]
    assert len(contents) == 1
    assert contents[0].parent_id == result.nodes[0].node_id
    chapters = [node for node in result.nodes if node.node_type == "chapter"]
    assert len(chapters) == 1
    assert contents[0].end_exclusive == chapters[0].position
    reused = resolve_structure(document_id="doc", document=document,
        bookmarks=[_bookmark("contents", "Contents", 1, 1, 0),
                   _bookmark("one", "Chapter One", 10, 1, 1)], toc_candidates=toc)
    assert len([node for node in reused.nodes if node.node_type == "contents"]) == 1


def test_v4_contents_and_bookmark_tree_publish_without_duplicate_chapters():
    from app.services.book_map_v4 import build_book_map_v4
    document = _document()
    toc = [TocCandidate("Chapter One", "1", 10, 0, "b0", 0,
        evidence_id="toc-first", mapped_physical_page=10, mapping_status="mapped")]
    resolution = resolve_structure(document_id="doc", document=document,
        bookmarks=[_bookmark("contents", "Contents", 10, 1, 0, y=0),
                   _bookmark("one", "Chapter One", 10, 1, 1, y=10),
                   _bookmark("two", "Chapter Two", 100, 1, 2, y=10)], toc_candidates=toc)
    book_map = build_book_map_v4(document_id="doc", source_sha256="0" * 64,
                                 document=document, resolution=resolution)
    assert len([node for node in book_map.nodes if node.node_type == "contents"]) == 1
    assert len([node for node in book_map.nodes if node.node_type == "chapter"]) == 2


def test_unparsed_page_uses_enclosing_bookmark_parent():
    from app.services.book_map_v4 import build_book_map_v4
    document = _document()
    document.pages.append(NormalizedPage("p60", 60, "60"))
    resolution = resolve_structure(document_id="doc", document=document,
        bookmarks=[_bookmark("one", "Chapter One", 10, 1, 0),
                   _bookmark("two", "Chapter Two", 100, 1, 1)])
    book_map = build_book_map_v4(document_id="doc", source_sha256="0" * 64,
                                 document=document, resolution=resolution)
    region = next(node for node in book_map.nodes if node.node_type == "unparsed_region"
                  and node.position.physical_page == 60)
    chapter = next(node for node in book_map.nodes if node.id == region.parent_id)
    assert chapter.title == "Chapter One"


def test_same_page_coordinate_boundaries_override_inconsistent_reading_order():
    from shreckdoc.structure_content import attach_content
    document = NormalizedDocument([NormalizedPage("p1", 1, "1")], [], [
        NormalizedBlock("body", "root", "paragraph", "After second chapter", [1], ["1"],
                        [{"left": 10, "top": 80, "right": 100, "bottom": 90}], 0, "body")])
    result = resolve_structure(document_id="doc", document=document, bookmarks=[
        _bookmark("a", "Chapter A", 1, 1, 0, y=10),
        _bookmark("b", "Chapter B", 1, 1, 1, y=60)])
    owners = attach_content(document, result).block_owner_ids
    assert owners["body"] == next(node.node_id for node in result.nodes if node.title == "Chapter B")


def test_multi_page_contents_ranges_do_not_move_bookmark_chapter_boundaries():
    from app.services.book_map_v4 import build_book_map_v4
    from shreckdoc.structure_content import attach_content, project_resolved_document

    page_numbers = (8, 9, 28, 51, 52)
    document = NormalizedDocument(
        [NormalizedPage(f"p{page}", page, str(page)) for page in page_numbers], [],
        [NormalizedBlock(
            f"b{page}", "stale-parser-section", "paragraph", f"Evidence on page {page}",
            [page], [str(page)], [{"left": 10, "top": 10, "right": 300, "bottom": 20}],
            order, f"ref{page}",
        ) for order, page in enumerate(page_numbers)],
    )
    resolution = resolve_structure(document_id="doc", document=document, bookmarks=[
        _bookmark("contents", "Contents", 8, 1, 0, y=0),
        _bookmark("three", "Chapter 3", 28, 1, 1, y=0),
        _bookmark("four", "Chapter 4", 52, 1, 2, y=0),
    ])
    structural = _by_title(resolution)
    boundary = structural["Chapter 3"].end_exclusive
    owners = attach_content(document, resolution).block_owner_ids
    assert owners["b8"] == owners["b9"] == structural["Contents"].node_id
    assert owners["b28"] == owners["b51"] == structural["Chapter 3"].node_id
    assert owners["b52"] == structural["Chapter 4"].node_id
    projected = project_resolved_document(document, resolution)
    assert {block.block_id: block.section_id for block in projected.blocks} == owners

    tree = build_book_map_v4(document_id="doc", source_sha256="a" * 64,
                             document=document, resolution=resolution)
    nodes = {node.id: node for node in tree.nodes}
    assert nodes[structural["Contents"].node_id].pages.model_dump() == {"start": 8, "end": 9}
    assert nodes[structural["Chapter 3"].node_id].pages.model_dump() == {"start": 28, "end": 51}
    assert nodes[structural["Chapter 4"].node_id].pages.model_dump() == {"start": 52, "end": 52}
    page52 = next(node for node in tree.nodes if node.source_block_ids == ["b52"])
    assert page52.parent_id == structural["Chapter 4"].node_id
    assert structural["Chapter 3"].end_exclusive == boundary
