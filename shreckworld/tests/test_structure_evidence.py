from types import SimpleNamespace

from app.services.pdf_structure_evidence import collect_pdf_structure
from shreckdoc.pipeline import NormalizedBlock, NormalizedDocument, NormalizedPage, NormalizedSection
from shreckdoc.structure_headings import infer_heading_candidates
from shreckdoc.structure_toc import TocCandidate, extract_toc_candidates, map_printed_pages


def _document(rows):
    blocks = []
    for index, row in enumerate(rows):
        text, page = row[:2]
        x = row[2] if len(row) > 2 else 0.1
        y = row[3] if len(row) > 3 else index * 0.03
        kind = row[4] if len(row) > 4 else "paragraph"
        native = row[5] if len(row) > 5 else {}
        blocks.append(NormalizedBlock(
            f"b{index}", "root", kind, text, [page], [],
            [{"left": x, "top": y, "right": x + 0.3, "bottom": y + 0.02}],
            index, f"ref-{index}", {"native": native}, native_type=None,
        ))
    pages = [NormalizedPage(f"p{number}", number, None, width=1, height=1) for number in sorted({row[1] for row in rows})]
    return NormalizedDocument(pages, [NormalizedSection("root", None, None, 0)], blocks)


class _Point:
    def __init__(self, x, y):
        self.x, self.y = x, y


class _Page:
    def get_label(self):
        return "iv"


class _Pdf:
    def __init__(self, toc):
        self.toc = toc
        self.closed = False

    def __len__(self):
        return 4

    def __getitem__(self, index):
        return _Page()

    def get_toc(self, simple=False):
        assert simple is False
        return self.toc

    def close(self):
        self.closed = True


def test_pdf_outline_preserves_nested_duplicate_titles_and_same_page_anchors(tmp_path):
    pdf = _Pdf([
        [1, "Repeated", 2, {"kind": 1, "to": _Point(25, 40), "xref": 11}],
        [2, "Nested", 2, {"kind": 1, "to": _Point(20, 90), "xref": 12}],
        [1, "Repeated", 3, {"kind": 1, "to": _Point(15, 5), "xref": 13}],
    ])
    result = collect_pdf_structure(tmp_path / "source.pdf", opener=lambda _: pdf)
    assert [entry.path for entry in result.outline] == [[0], [0, 1], [2]]
    assert [entry.physical_page for entry in result.outline] == [2, 2, 3]
    assert [entry.title for entry in result.outline].count("Repeated") == 2
    assert result.outline[0].x == 25 and result.outline[0].y == 40
    assert result.page_labels == {1: "iv", 2: "iv", 3: "iv", 4: "iv"}
    assert pdf.closed


def test_pdf_outline_reads_real_generated_pdf_without_using_page_text(tmp_path):
    import pymupdf

    path = tmp_path / "outlined.pdf"
    document = pymupdf.open()
    document.new_page()
    document.new_page()
    document.set_toc([[1, "Chapter", 2], [2, "Same page subsection", 2]])
    document.save(path)
    document.close()
    result = collect_pdf_structure(path)
    assert result.physical_page_count == 2
    assert [item.physical_page for item in result.outline] == [2, 2]
    assert result.outline[0].x is not None and result.outline[0].y is not None
    assert "pdf_page_labels_unavailable" in result.diagnostics
    assert "pdf_page_label_missing:1" in result.diagnostics


def test_pdf_outline_retains_external_named_and_invalid_destinations(tmp_path):
    result = collect_pdf_structure(tmp_path / "source.pdf", opener=lambda _: _Pdf([
        [1, "External", 0, {"kind": 3, "uri": "https://example.test"}],
        [1, "Named", 0, {"kind": 4, "nameddest": "chapter-start"}],
        [1, "Invalid", 99, {"kind": 1}],
    ]))
    assert len(result.outline) == 3
    assert result.outline[0].physical_page is None
    assert "non_local_destination" in result.outline[0].diagnostics
    assert result.outline[1].destination_name == "chapter-start"
    assert "named_destination_not_resolved" in result.outline[1].diagnostics
    assert "unresolved_or_invalid_destination" in result.outline[2].diagnostics


def test_pdf_structure_missing_metadata_and_empty_outline_are_diagnostics(tmp_path):
    class NoMetadata(_Pdf):
        def get_toc(self, simple=False):
            raise RuntimeError("no outline")

    result = collect_pdf_structure(tmp_path / "source.pdf", opener=lambda _: NoMetadata([]))
    assert not result.outline
    assert "pdf_outline_unavailable:RuntimeError" in result.diagnostics
    unavailable = collect_pdf_structure(tmp_path / "missing.pdf", opener=lambda _: (_ for _ in ()).throw(OSError()))
    assert unavailable.physical_page_count is None and unavailable.diagnostics


def test_toc_extraction_handles_columns_multiline_frontmatter_and_late_contents():
    document = _document([
        ("Contents", 2, 0.1, 0.02),
        ("The first", 2, 0.1, 0.10),
        ("entry .......... iv", 2, 0.1, 0.13),
        ("Second Entry .......... 2", 2, 0.1, 0.20),
        ("Third Entry .......... 3", 2, 0.6, 0.10),
        ("Fourth Entry .......... 4", 2, 0.6, 0.20),
        ("Fifth Entry .......... 5", 12, 0.1, 0.7),
        ("Repeated Label A .......... 6", 12, 0.1, 0.8),
        ("Repeated Label B .......... 6", 12, 0.1, 0.9),
    ])
    extraction = extract_toc_candidates(document)
    assert [(row.title, row.printed_label) for row in extraction.candidates] == [
        ("The first entry", "iv"), ("Second Entry", "2"),
        ("Third Entry", "3"), ("Fourth Entry", "4"), ("Fifth Entry", "5"),
        ("Repeated Label A", "6"), ("Repeated Label B", "6"),
    ]
    assert {row.column for row in extraction.candidates if row.physical_page == 2} == {0, 1}
    assert extraction.candidates[-1].physical_page == 12
    assert all(row.source_block_id for row in extraction.candidates)
    assert extraction.candidates[0].evidence_id and extraction.candidates[0].supporting_block_ids


def test_toc_extraction_keeps_partial_dotted_rows_and_rejects_ordinary_dotted_prose():
    partial = extract_toc_candidates(_document([("Appendix .......... 22", 8)]))
    prose = extract_toc_candidates(_document([("The stars.... bright above the valley.", 1)]))
    assert [(row.title, row.printed_label) for row in partial.candidates] == [("Appendix", "22")]
    assert not prose.candidates
    assert prose.diagnostics == ["toc_candidates_not_found"]


def test_printed_page_mapping_uses_unique_anchors_offsets_and_keeps_ambiguity():
    candidates = [TocCandidate(title, label, 1, index, f"b{index}", 0) for index, (title, label) in enumerate([
        ("Roman One", "i"), ("Roman Three", "iii"), ("Roman Two", "ii"),
        ("Arabic One", "1"), ("Arabic Three", "3"), ("Arabic Two", "2"),
        ("Missing", "4"), ("Twin A", "9"), ("Twin B", "9"),
    ])]
    headings = [SimpleNamespace(title="Roman One", physical_page=2), SimpleNamespace(title="Roman Three", physical_page=4),
                SimpleNamespace(title="Arabic One", physical_page=6), SimpleNamespace(title="Arabic Three", physical_page=8),
                SimpleNamespace(title="Twin A", physical_page=10), SimpleNamespace(title="Twin B", physical_page=11)]
    result = map_printed_pages(candidates, headings)
    by_title = {row.title: row for row in result.candidates}
    assert by_title["Roman Two"].mapped_physical_page == 3
    assert by_title["Arabic Two"].mapped_physical_page == 7
    assert by_title["Missing"].mapped_physical_page is None
    assert by_title["Twin A"].mapping_reason == "duplicate_printed_label"
    assert by_title["Twin B"].mapping_status == "ambiguous"


def test_printed_page_mapping_rejects_conflicting_piecewise_offsets():
    candidates = [TocCandidate(title, label, 1, index, f"b{index}", 0) for index, (title, label) in enumerate([
        ("One A", "1"), ("Four A", "4"), ("Two B", "2"), ("Five B", "5"), ("Three", "3"),
    ])]
    headings = [SimpleNamespace(title="One A", physical_page=1), SimpleNamespace(title="Four A", physical_page=4),
                SimpleNamespace(title="Two B", physical_page=12), SimpleNamespace(title="Five B", physical_page=15)]
    result = map_printed_pages(candidates, headings)
    assert result.candidates[-1].mapping_status == "ambiguous"
    assert result.candidates[-1].mapping_reason == "conflicting_piecewise_offsets"


def test_heading_profiles_numbering_running_headers_dialogue_tables_and_sparse_evidence():
    document = _document([
        ("Chapter 1: Opening", 1, 0.1, 0.1, "heading", {"formatting": {"font_size": 24, "bold": True}}),
        ("1.2.3.4.5.6.7 Deep Rule", 2, 0.1, 0.1, "paragraph"),
        ("A Split\nTitle", 3, 0.1, 0.1, "heading", {"formatting": {"font_size": 18, "bold": True}}),
        ('“What now?” she asked.', 4, 0.1, 0.1, "paragraph"),
        ("Chapter 8: Table cell", 4, 0.1, 0.1, "table"),
        ("RUNNING TITLE", 1, 0.1, 0.8, "paragraph", {"formatting": {"font_size": 8}}),
        ("RUNNING TITLE", 2, 0.1, 0.8, "paragraph", {"formatting": {"font_size": 8}}),
        ("RUNNING TITLE", 3, 0.1, 0.8, "paragraph", {"formatting": {"font_size": 8}}),
    ])
    document.sections.extend([
        NormalizedSection("large-style", "root", "Parser level is wrong", 9),
        NormalizedSection("small-style", "root", "Parser level is wrong", 3),
    ])
    document.blocks[0].section_id = "large-style"
    document.blocks[2].section_id = "small-style"
    result = infer_heading_candidates(document)
    by_text = {candidate.title: candidate for candidate in result.candidates}
    assert result.sufficient_evidence
    assert by_text["Deep Rule"].level == 7
    assert by_text["A Split Title"].disposition == "candidate"
    assert by_text["Opening"].level == 1 and by_text["A Split Title"].level == 2
    assert '“What now?” she asked.' not in by_text
    assert "RUNNING TITLE" not in by_text or by_text["RUNNING TITLE"].disposition == "rejected"
    assert "Chapter 8: Table cell" not in by_text
    assert any(profile.median_font_size for profile in result.profiles)
    assert len(infer_heading_candidates(_document([("One Heading", 1, 0.1, 0.1, "heading")])).diagnostics) == 1


def test_heading_inference_can_exclude_known_toc_source_blocks_without_deleting_them():
    document = _document([("Chapter One ........ 5", 1), ("1. Chapter Body", 5)])
    result = infer_heading_candidates(document, toc_source_block_ids={"b0"})
    assert {row.block_id for row in result.candidates} == {"b1"}
    assert len(document.blocks) == 2
