import sys
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services.document_parsing import parse_source
from app.services.parser_cache import cache_identity
from app.services.pymupdf_parsing import parse_pdf
from semantic_helpers import WordTokenizer
from shreckdoc.pipeline import build_chunks
from test_document_metadata import pdf_bytes


def test_layout_chunks_are_normalized_cached_and_split_as_children(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(pdf_bytes())
    monkeypatch.setenv("SHRECKWORLD_PDF_PARSER", "pymupdf")
    monkeypatch.setattr("app.services.document_parsing.version", lambda _: "2.1")
    chunk_calls = []

    def chunk(identifier, text, section, path, types):
        return SimpleNamespace(id=identifier, text=text, metadata=SimpleNamespace(
            section_id=section, section_path=path, page_start=1, page_end=1,
            bboxes=[(1, 10, 20, 200, 80)], types=types, element_ids=["p1.b0"],
            table_ids=["t0"] if "table" in types else [], figure_ids=[]))

    class PDF:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def __iter__(self):
            return iter([SimpleNamespace(rect=SimpleNamespace(width=600, height=800))])
        def __len__(self):
            return 1
        def to_chunks(self, **kwargs):
            chunk_calls.append(kwargs)
            class Result:
                diagnostics = {"page_count": 1, "figures_without_text": ["f0"]}
                sections = [SimpleNamespace(id="s0", title="Combat", heading_element_id="p1.b0")]
                elements = [SimpleNamespace(id="p1.b0", page=1)]
                def __iter__(self):
                    return iter([
                        chunk("c0", "# Combat\nAttack with a die.", "s0", ["Combat"], ["heading", "paragraph"]),
                        chunk("c1", "| Roll | Result |\n| --- | --- |\n| 1 | Miss |", "s0", ["Combat"], ["table"]),
                    ])
            return Result()

    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda _: PDF()))
    first = parse_source(source, ingestion_id="first", output_dir=tmp_path / "first", heartbeat=lambda _: None)
    second = parse_source(source, ingestion_id="second", output_dir=tmp_path / "second", heartbeat=lambda _: None)
    assert chunk_calls == [{"use_ocr": False, "table_mode": "isolate", "merge_small_chunks": False}]
    assert not first.cache_hit and second.cache_hit
    assert first.parser_name == "pymupdf" and first.quality["pymupdf_pages"] == 1
    assert first.diagnostics == second.diagnostics == ["PyMuPDF figures_without_text: ['f0']"]
    assert [block.block_type for block in first.document.blocks] == ["heading", "paragraph", "table"]
    assert first.document.sections[1].heading_path == ["Combat"]
    assert first.document.blocks[1].display_text == "Attack with a die."
    assert first.document.blocks[2].metadata["pymupdf_chunk"]["table_ids"] == ["t0"]
    assert first.document.blocks[1].bounding_boxes[0]["coord_origin"] == "TOPLEFT"
    assert first.document.blocks[1].block_id != second.document.blocks[1].block_id
    chunks = build_chunks(document=first.document, ingestion_id="first", library_item_id="item",
                          ontology_id="scope", book_title="Book", rpg_system="", tokenizer=WordTokenizer(),
                          max_embedding_tokens=100, preserve_unit_boundaries=True)
    children = [chunk for chunk in chunks if chunk.chunk_role == "child"]
    assert len(children) == 2
    assert all(child.parent_chunk_id == chunks[0].chunk_id for child in children)
    assert {child.content_type for child in children} == {"narrative", "table"}
    assert (tmp_path / "second" / "pymupdf_document.json").exists()
    assert cache_identity(source, Settings(), "2.1", parser_name="pymupdf") != cache_identity(source, Settings(), "2.1")


def test_missing_chunk_api_is_an_explicit_failure(tmp_path, monkeypatch):
    class PDF:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda _: PDF()))
    with pytest.raises(RuntimeError, match="requires PyMuPDF 2.1"):
        parse_pdf(tmp_path / "source.pdf")


def test_invalid_parser_setting_is_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(pdf_parser="unknown")


def test_real_pymupdf_layout_extraction(tmp_path, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    if not hasattr(pymupdf.Document, "to_chunks"):
        pytest.skip("PyMuPDF 2.1 is only available on Python 3.11+")
    source = tmp_path / "sample.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Combat Rules", fontsize=20)
        page.insert_text((72, 105), "When fighting, roll one die.", fontsize=11)
        pdf.save(source)
    monkeypatch.setenv("SHRECKWORLD_PDF_PARSER", "pymupdf")
    parsed = parse_source(source, ingestion_id="real", output_dir=tmp_path / "parsed", heartbeat=lambda _: None)
    assert parsed.parser_name == "pymupdf"
    assert len(parsed.document.pages) == 1
    assert any("When fighting, roll one die." in block.display_text for block in parsed.document.blocks)
    assert any(block.bounding_boxes for block in parsed.document.blocks if block.block_type != "heading")
