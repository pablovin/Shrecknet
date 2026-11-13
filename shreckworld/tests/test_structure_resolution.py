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


def test_chapter_bookmarks_enclose_heading_subsections_and_page_104_stays_in_chapter_two():
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
    assert by_title["Opening"].parent_id == by_title["Chapter One"].node_id
    assert by_title["Interior heading"].parent_id == by_title["Chapter Two"].node_id
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
