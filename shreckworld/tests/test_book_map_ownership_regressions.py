"""Regressions for reported Call of Cthulhu pages, using synthetic source text.

The source PDF and exported Bookmap are not distributed with this repository.
Synthetic cases reproduce their reported destinations and provenance. An optional
local-artifact regression verifies the retained extraction when available.
"""
from copy import deepcopy

import pytest

from shreckdoc.pipeline import NormalizedBlock, NormalizedDocument, NormalizedPage, build_chunks
from shreckdoc.structure_content import attach_content, project_resolved_document, segment_boundary_content
from shreckdoc.structure_resolution import resolve_structure
from shreckdoc.structure_toc import TocCandidate
from shreckdoc.structure_types import TableCell, TableStructure
from shreckdoc.structure_validation import validate_book_map
from shreckdoc.table_text import serialize_table_text
from app.services.book_map_v4 import build_book_map_v4
from semantic_helpers import WordTokenizer
from test_structure_resolution import _bookmark


def document(blocks):
    return NormalizedDocument([NormalizedPage(f"p{page}", page, str(page))
        for page in sorted({page for block in blocks for page in block.page_numbers})], [], blocks)


def block(identity, pages, text="Source paragraph", kind="paragraph", provenance=None):
    return NormalizedBlock(identity, "parser-section", kind, text, pages, [str(p) for p in pages],
        [], pages[0], identity, {"provenance": provenance or [{"page_no": page} for page in pages]})


def tree_for(source, resolution, **kwargs):
    return build_book_map_v4(document_id="coc", source_sha256="a" * 64,
        document=source, resolution=resolution, **kwargs)


def chapters(source):
    return resolve_structure(document_id="coc", document=source, bookmarks=[
        _bookmark("13", "Chapter 13", 275, 1, 0),
        _bookmark("14", "Chapter 14", 276, 1, 1)])


def test_call_of_cthulhu_false_toc_page35_stays_in_chapter3():
    source = document([block(f"b{page}", [page]) for page in (8, 9, 28, 35)])
    toc = [TocCandidate("Chapter 3", "28", 35, 0, "b35", 0,
        evidence_id="false-toc", mapped_physical_page=28, mapping_status="mapped")]
    resolution = resolve_structure(document_id="coc", document=source, bookmarks=[
        _bookmark("contents", "Contents", 8, 1, 0),
        _bookmark("3", "Chapter 3", 28, 1, 1)], toc_candidates=toc)
    tree = tree_for(source, resolution)
    contents = next(node for node in tree.nodes if node.node_type == "contents")
    assert (contents.pages.start, contents.pages.end) == (8, 9)
    paragraph = next(node for node in tree.nodes if node.source_block_ids == ["b35"])
    assert next(node for node in tree.nodes if node.id == paragraph.parent_id).title == "Chapter 3"
    assert "toc_evidence_outside_contents" in paragraph.validation_warnings
    assert next(row for row in tree.evidence if row.id == "false-toc").diagnostics[-1].rejected
    assert next(b for b in project_resolved_document(source, resolution).blocks if b.block_id == "b35").section_id == paragraph.parent_id


@pytest.mark.parametrize("spans", [None, [(0, 5), (0, 5)], [(0, 4), (5, 10)], [(0, 5), (5, 11)]])
def test_call_of_cthulhu_unresolved_pages275_278_do_not_extend_chapter13(spans):
    provenance = [{"page_no": page, **({"charspan": span} if spans else {})}
        for page, span in zip((275, 278), spans or [None, None])]
    source = document([block("crossing", [275, 278], "abcdefghij", provenance=provenance)])
    resolution = chapters(source)
    prepared = segment_boundary_content(source, resolution)
    assert prepared.blocks == source.blocks
    tree = tree_for(prepared, resolution)
    paragraph = next(node for node in tree.nodes if node.source_block_ids == ["crossing"])
    assert paragraph.parent_id == tree.root_id
    assert paragraph.resolution_status == "unresolved"
    assert "unresolved_cross_boundary_provenance" in paragraph.validation_warnings
    assert paragraph.id in tree.quality.unresolved_node_ids
    assert [loc.physical_page for loc in paragraph.page_locators] == [275, 278]
    assert next(node for node in tree.nodes if node.title == "Chapter 13").pages.end == 275
    assert not validate_book_map(tree)


def test_call_of_cthulhu_exact_source_spans_split_before_ownership_and_chunking():
    provenance = [{"page_no": 275, "charspan": [0, 5]}, {"page_no": 278, "charspan": [5, 10]}]
    source = document([block("crossing", [275, 278], "abcdeFGHIJ", provenance=provenance)])
    resolution = chapters(source)
    prepared = segment_boundary_content(source, resolution)
    assert "".join(b.display_text for b in prepared.blocks) == source.blocks[0].display_text
    assert len(prepared.blocks) == 2
    assert segment_boundary_content(prepared, resolution) == prepared
    for part in prepared.blocks:
        assert part.docling_reference == "crossing"
        assert part.metadata["source_segmentation"]["original_provenance"] == provenance
    projected = project_resolved_document(prepared, resolution)
    chunks = build_chunks(document=projected, ingestion_id="coc", library_item_id="book",
        ontology_id="game", book_title="Call of Cthulhu", rpg_system="CoC",
        tokenizer=WordTokenizer(), preserve_unit_boundaries=True)
    mapping = {b: [c.chunk_id] for c in chunks if c.chunk_role == "child" for b in c.source_block_ids}
    tree = tree_for(prepared, resolution, chunk_ids_by_block=mapping, index_alignment="aligned")
    for part, title in zip(prepared.blocks, ("Chapter 13", "Chapter 14")):
        node = next(n for n in tree.nodes if n.source_block_ids == [part.block_id])
        assert next(n for n in tree.nodes if n.id == node.parent_id).title == title
        assert node.chunk_ids


def test_validation_rejects_wrong_bookmark_owner_even_with_expanded_parent_ranges():
    source = document([block("a", [275]), block("b", [278])])
    resolution = chapters(source)
    tree = tree_for(source, resolution)
    bad = deepcopy(tree)
    c13 = next(n for n in bad.nodes if n.title == "Chapter 13")
    paragraph = next(n for n in bad.nodes if n.source_block_ids == ["b"])
    paragraph.structural_node_id = c13.id
    assert any("outside authoritative bookmark interval" in error for error in validate_book_map(bad))


@pytest.mark.parametrize("page", [9, 50, 51, 406])
def test_reported_table_pages_reliable_numeric_markdown_is_searchable(page):
    text = "| Roll | Value |\n| --- | --- |\n| 01–05 | 100 |\n| 06–10 |  |"
    source = document([block("table", [page], text, "table")])
    resolution = resolve_structure(document_id="coc", document=source,
        bookmarks=[_bookmark("chapter", "Chapter", page, 1, 0)])
    serialized = serialize_table_text(None, text)
    assert serialized.embedding_eligible
    projected = project_resolved_document(source, resolution)
    chunks = build_chunks(document=projected, ingestion_id="coc", library_item_id="book",
        ontology_id="game", book_title="Call of Cthulhu", rpg_system="CoC", tokenizer=WordTokenizer(),
        preserve_unit_boundaries=True, table_text_by_block={"table": serialized.text},
        table_row_groups_by_block={"table": serialized.row_groups})
    children = [c for c in chunks if c.chunk_role == "child"]
    assert children and all("table" in c.source_block_ids for c in children)
    assert "100" in " ".join(c.embedding_text for c in children)
    assert "| 06–10 |  |" in "\n".join(c.display_text for c in children)
    tree = tree_for(source, resolution, chunk_ids_by_block={"table": [c.chunk_id for c in children]})
    assert next(n for n in tree.nodes if n.node_type == "table").chunk_ids


@pytest.mark.parametrize("text,structure,warning", [
    ("", None, "table_no_recoverable_text"),
    ("Unreliable cell dump 1 2", None, "table_text_conversion_unreliable"),
    ("", TableStructure(cells=[TableCell("Roll", 0, 0), TableCell("100", 1, 0)]), "table_source_chunk_mapping_missing"),
])
def test_missing_table_chunks_are_classified(text, structure, warning):
    source = document([block("table", [50], text, "table")])
    source.blocks[0].table_structure = structure
    resolution = resolve_structure(document_id="coc", document=source,
        bookmarks=[_bookmark("chapter", "Chapter", 50, 1, 0)])
    tree = tree_for(source, resolution, chunk_ids_by_block={})
    assert warning in next(n for n in tree.nodes if n.node_type == "table").validation_warnings


@pytest.mark.parametrize("text", ["a | b\n1 | 2", "| A | B |\n| --- | --- |\n| 1 |", "1 2 3"])
def test_ambiguous_table_text_is_not_promoted(text):
    assert not serialize_table_text(None, text).embedding_eligible


@pytest.fixture(scope="module")
def local_call_of_cthulhu_artifacts():
    """Use retained local rulebook artifacts without committing copyrighted source."""
    import json
    import os
    from pathlib import Path
    from shreckdoc.pipeline import NormalizedSection
    from shreckdoc.structure_resolution import StructuralCandidate, StructuralResolution
    from shreckdoc.structure_types import EvidenceDiagnostic, SourcePosition, StructuralEvidence

    configured = os.environ.get("SHRECKWORLD_COC_ARTIFACT_DIR")
    candidates = [Path(configured) / "book_map_v4.json"] if configured else (
        Path(__file__).resolve().parents[1] / "media").rglob("book_map_v4.json")
    for path in candidates:
        tree = json.loads(path.read_text())
        missing_pages = {n["pages"]["start"] for n in tree["nodes"]
                         if n["node_type"] == "table" and not n["chunk_ids"]}
        if not {9, 50, 51, 406} <= missing_pages:
            continue
        data = json.loads((path.parent / "normalized_document.json").read_text())
        blocks = []
        for row in data["blocks"]:
            table = row.get("table_structure")
            if table:
                row["table_structure"] = TableStructure(**{**table, "cells": [TableCell(**c) for c in table["cells"]]})
            blocks.append(NormalizedBlock(**row))
        source = NormalizedDocument(
            [NormalizedPage(**{**row, "accounting": None}) for row in data["pages"]],
            [NormalizedSection(**row) for row in data["sections"]], blocks)
        stored = json.loads((path.parent / "book_map_evidence.json").read_text())["resolution"]
        nodes = [StructuralCandidate(**{**row, "position": SourcePosition(**row["position"]),
            "end_exclusive": SourcePosition(**row["end_exclusive"]) if row["end_exclusive"] else None})
            for row in stored["nodes"]]
        evidence = [StructuralEvidence(row["id"], row["source_kind"], row["original_reference"],
            SourcePosition(**row["locator"]), observed_features=row["observed_features"],
            diagnostics=[EvidenceDiagnostic(**d) for d in row["diagnostics"]])
            for row in tree["evidence"] if row["source_kind"] in {"bookmark", "heading", "toc_entry"}]
        resolution = StructuralResolution(tree["document_id"], nodes, evidence,
            stored["diagnostics"], stored["conflicts"], True)
        return source, resolution, tree["source_sha256"]
    pytest.skip("Call of Cthulhu extraction not installed; set SHRECKWORLD_COC_ARTIFACT_DIR")


def test_actual_call_of_cthulhu_ownership_and_table_recovery(local_call_of_cthulhu_artifacts):
    source, resolution, digest = local_call_of_cthulhu_artifacts
    crossing = next(b for b in source.blocks if b.page_numbers == [275, 278])
    prepared = segment_boundary_content(source, resolution)
    parts = [b for b in prepared.blocks if b.metadata.get("source_segmentation", {}).get("original_block_id") == crossing.block_id]
    assert len(parts) == 3
    assert "".join(b.display_text for b in parts) == crossing.display_text
    tree = build_book_map_v4(document_id=resolution.document_id, source_sha256=digest,
        document=prepared, resolution=resolution)
    by_id = {n.id: n for n in tree.nodes}
    contents = next(n for n in tree.nodes if n.node_type == "contents")
    assert (contents.pages.start, contents.pages.end) == (8, 9)
    page35 = [n for n in tree.nodes if n.source_block_ids and n.position.physical_page == 35]
    assert page35
    def chapter_owner(node):
        while node.level > 1:
            node = by_id[node.parent_id]
        return node

    assert all(chapter_owner(n).title.startswith("3.") for n in page35)
    for part in parts:
        node = next(n for n in tree.nodes if part.block_id in n.source_block_ids)
        owner = chapter_owner(node)
        assert owner.title.startswith("13." if part.page_numbers == [275] else "14.")
    table_results = {b.page_numbers[0]: serialize_table_text(b.table_structure, b.display_text)
        for b in prepared.blocks if b.block_type == "table" and b.page_numbers in ([9], [50], [51], [406])}
    assert table_results[9].embedding_eligible
    assert table_results[406].embedding_eligible
    assert not table_results[50].text and not table_results[51].text
    projected = project_resolved_document(prepared, resolution)
    tables = [b for b in projected.blocks if b.block_type == "table" and b.page_numbers in ([9], [50], [51], [406])]
    table_document = NormalizedDocument(projected.pages, projected.sections, tables)
    serialized = {b.block_id: serialize_table_text(b.table_structure, b.display_text) for b in tables}
    chunks = build_chunks(document=table_document, ingestion_id=resolution.document_id,
        library_item_id="book", ontology_id="game", book_title="Call of Cthulhu", rpg_system="CoC",
        tokenizer=WordTokenizer(), preserve_unit_boundaries=True, max_embedding_tokens=100,
        table_text_by_block={identity: value.text if value.embedding_eligible else ""
                            for identity, value in serialized.items()})
    children = [chunk for chunk in chunks if chunk.chunk_role == "child"]
    assert {page for chunk in children for page in chunk.physical_page_numbers} == {9, 406}
    import re
    original_numbers = set(re.findall(r"\d+", next(b.display_text for b in tables if b.page_numbers == [406])))
    indexed_numbers = set(re.findall(r"\d+", " ".join(c.display_text for c in children)))
    assert original_numbers <= indexed_numbers
    assert not validate_book_map(tree)


@pytest.mark.parametrize("text", ["", "Unreliable cell dump 1 2"])
def test_empty_native_cells_cannot_make_fallback_text_reliable(text):
    structure = TableStructure(cells=[TableCell("", 0, 0)])
    assert not serialize_table_text(structure, text).embedding_eligible


def test_single_cell_reference_export_retains_only_observed_text():
    structure = TableStructure(cells=[TableCell("", 0, 0)])
    text = "| Damage 1D6; Range 100 |\n| ------------------- |"
    serialized = serialize_table_text(structure, text)
    assert serialized.embedding_eligible
    assert serialized.text == text
    assert not serialized.row_groups


def test_whitespace_between_source_spans_is_preserved():
    source = document([block("crossing", [275, 278], "first second", provenance=[
        {"page_no": 275, "charspan": [0, 5]}, {"page_no": 278, "charspan": [6, 12]}])])
    prepared = segment_boundary_content(source, chapters(source))
    assert [b.display_text for b in prepared.blocks] == ["first ", "second"]


def test_validation_requires_cross_boundary_warning_and_unresolved_root_owner():
    source = document([block("crossing", [275, 278])])
    tree = tree_for(source, chapters(source))
    paragraph = next(n for n in tree.nodes if n.source_block_ids == ["crossing"])
    paragraph.validation_warnings = []
    assert any("unreported cross-boundary provenance" in error for error in validate_book_map(tree))


def test_legacy_maps_remain_readable_until_explicit_reindex():
    from app.services.book_map_contract import read_book_map
    source = document([block("crossing", [275, 278])])
    tree = tree_for(source, chapters(source))
    paragraph = next(n for n in tree.nodes if n.source_block_ids == ["crossing"])
    paragraph.validation_warnings = []
    tree.resolver_version = "book-map-v4-resolver-2"
    assert read_book_map(tree.model_dump()).document_id == tree.document_id
