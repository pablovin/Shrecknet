from __future__ import annotations

import json

import pytest

from app.services.docling_ingestion_service import (
    MAX_EMBEDDING_TOKENS,
    NormalizedBlock,
    NormalizedChunk,
    NormalizedDocument,
    NormalizedPage,
    NormalizedSection,
    _atomic_json,
    _page_label_map,
    _parse_quality_summary,
    _split_table_for_embedding,
    build_chunks,
    build_embedding_text,
    normalize_document,
    DoclingIngestionService,
)
from app.tasks.pdf_embedding import embed_pdf_book, embed_pdf_book_old
from app.graphrag.embedding_service import document_embedding_text, query_embedding_text


class _WordTokenizer:
    def encode(self, text: str, add_special_tokens: bool = True):
        tokens = text.replace("\n", " ").split()
        return ([0] if add_special_tokens else []) + list(range(len(tokens))) + ([1] if add_special_tokens else [])


class _WindowTokenizer:
    """Small reversible tokenizer to exercise the true tokenizer-window fallback."""

    def __init__(self) -> None:
        self._tokens: list[str] = []

    def encode(self, text: str, add_special_tokens: bool = True):
        words = text.replace("\n", " ").split()
        ids: list[int] = []
        for word in words:
            if word not in self._tokens:
                self._tokens.append(word)
            ids.append(self._tokens.index(word) + 2)
        return ([0] if add_special_tokens else []) + ids + ([1] if add_special_tokens else [])

    def __call__(self, text: str, add_special_tokens: bool = True, truncation: bool = False, verbose: bool = False):
        return {"input_ids": self.encode(text, add_special_tokens=add_special_tokens)}

    def decode(self, ids, skip_special_tokens: bool = True, clean_up_tokenization_spaces: bool = False):
        return " ".join(self._tokens[token_id - 2] for token_id in ids if token_id >= 2)


def test_normalize_document_uses_heading_boundaries_and_separate_page_labels(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.docling_ingestion_service._page_label_map",
        lambda _path, _count: {1: "i", 2: "1"},
    )
    items = [
        {"reference": "#/texts/0", "block_type": "heading", "text": "Characters", "level": 1, "reading_order": 0, "provenance": [{"page_no": 1, "bbox": {"left": 1}}]},
        {"reference": "#/texts/1", "block_type": "paragraph", "text": "Character introduction.", "reading_order": 1, "provenance": [{"page_no": 1, "bbox": {"left": 2}}]},
        {"reference": "#/texts/2", "block_type": "heading", "text": "Engineer", "level": 2, "reading_order": 2, "provenance": [{"page_no": 2, "bbox": {"left": 1}}]},
        {"reference": "#/texts/3", "block_type": "paragraph", "text": "Occupation Skill Points: EDU × 4.", "reading_order": 3, "provenance": [{"page_no": 2, "bbox": {"left": 2}}]},
        {"reference": "#/texts/4", "block_type": "heading", "text": "Entertainer", "level": 2, "reading_order": 4, "provenance": [{"page_no": 2, "bbox": {"left": 3}}]},
    ]
    normalized = normalize_document(
        ingestion_id="ing-1",
        pdf_path=tmp_path / "content.pdf",
        canonical={"pages": {"1": {"size": {"width": 100, "height": 200}}, "2": {"size": {"width": 100, "height": 200}}}},
        items=items,
    )

    engineer = next(section for section in normalized.sections if section.heading == "Engineer")
    entertainer = next(section for section in normalized.sections if section.heading == "Entertainer")
    engineer_text = [block.display_text for block in normalized.blocks if block.section_id == engineer.section_id]

    assert engineer.heading_path == ["Characters", "Engineer"]
    assert "Occupation Skill Points: EDU × 4." in engineer_text
    assert entertainer.section_id != engineer.section_id
    assert normalized.pages[0].physical_page_number == 1
    assert normalized.pages[0].displayed_page_label == "i"


def test_build_chunks_separates_display_and_context_and_obeys_token_limit() -> None:
    tokenizer = _WordTokenizer()
    section = NormalizedSection("s1", None, "Engineer", 1, ["Occupations", "Engineer"], ["b1"], [7], ["8"])
    document = NormalizedDocument(
        pages=[NormalizedPage("p1", 7, "8")],
        sections=[section],
        blocks=[
            NormalizedBlock(
                "b1", "s1", "paragraph",
                "Occupation Skill Points: EDU × 4. " + "Skill choice. " * 100,
                [7], ["8"], [{"left": 1}], 1,
            )
        ],
    )

    chunks = build_chunks(
        document=document,
        ingestion_id="ing-1",
        library_item_id=20,
        ontology_id=1,
        book_title="Investigator Handbook",
        rpg_system="Call of Cthulhu",
        tokenizer=tokenizer,
    )
    parent = next(chunk for chunk in chunks if chunk.chunk_role == "parent")
    children = [chunk for chunk in chunks if chunk.chunk_role == "child"]

    assert parent.embedding_eligible is False
    assert children
    assert all(child.parent_chunk_id == parent.chunk_id for child in children)
    assert all(len(tokenizer.encode(child.embedding_text)) <= MAX_EMBEDDING_TOKENS for child in children)
    assert all(child.embedding_text.startswith("passage: ") for child in children)
    assert all("Book: Investigator Handbook" in child.embedding_text for child in children)
    assert all("Book: Investigator Handbook" not in child.display_text for child in children)
    assert "×" in parent.display_text
    assert "multiplied by" in children[0].embedding_text


def test_oversized_single_sentence_uses_token_windows_instead_of_failing() -> None:
    tokenizer = _WindowTokenizer()
    section = NormalizedSection("s1", None, "Very Long Entry", 1, ["Rules", "Very Long Entry"], ["b1"], [1], ["1"])
    document = NormalizedDocument(
        pages=[NormalizedPage("p1", 1, "1")],
        sections=[section],
        blocks=[NormalizedBlock("b1", "s1", "paragraph", " ".join(f"word{index}" for index in range(800)), [1], ["1"], [], 1)],
    )

    chunks = build_chunks(
        document=document, ingestion_id="ing-window", library_item_id=20,
        ontology_id=1, book_title="Long Rules", rpg_system="Test System",
        tokenizer=tokenizer, max_embedding_tokens=60,
    )
    children = [chunk for chunk in chunks if chunk.chunk_role == "child"]

    assert len(children) > 1
    assert all(len(tokenizer.encode(child.embedding_text)) <= 60 for child in children)
    assert all(child.embedding_text.startswith("passage: ") for child in children)


def test_table_children_repeat_markdown_header() -> None:
    tokenizer = _WordTokenizer()
    prototype = NormalizedChunk(
        chunk_id="c", ingestion_id="i", library_item_id=1, ontology_id=1,
        chunk_role="child", content_type="table", display_text="", embedding_text="",
        book_title="Book", rpg_system="System", heading_path=["Equipment"],
        primary_heading="Weapons", block_types=["table"], physical_page_numbers=[2],
        displayed_page_labels=["3"], bounding_boxes=[], parent_chunk_id="p",
        parent_section_id="s", source_block_ids=["b"], chunk_index=1,
        embedding_eligible=True,
    )
    rows = [f"| item {index} | " + "value " * 20 + "|" for index in range(20)]
    table = "\n".join(["| Item | Value |", "|---|---|", *rows])

    pieces = _split_table_for_embedding(tokenizer, prototype, table)

    assert len(pieces) > 1
    assert all(piece.startswith("| Item | Value |\n|---|---|") for piece in pieces)


def test_embedding_serializer_is_deterministic_and_atomic_manifest_write(tmp_path) -> None:
    chunk = NormalizedChunk(
        chunk_id="c", ingestion_id="i", library_item_id=1, ontology_id=1,
        chunk_role="child", content_type="formula", display_text="Damage ≤ EDU × 2",
        embedding_text="", book_title="Rules", rpg_system="CoC",
        heading_path=["Combat", "Damage"], primary_heading="Damage",
        block_types=["formula"], physical_page_numbers=[4], displayed_page_labels=["5"],
        bounding_boxes=[], parent_chunk_id="p", parent_section_id="s",
        source_block_ids=["b"], chunk_index=2, embedding_eligible=True,
    )
    first = build_embedding_text(chunk)
    second = build_embedding_text(chunk)
    path = tmp_path / "ingestion_manifest.json"
    _atomic_json(path, {"embedding_text": first})

    assert first == second
    assert "Damage less than or equal to EDU multiplied by 2" in first
    assert json.loads(path.read_text(encoding="utf-8"))["embedding_text"] == first
    assert not path.with_suffix(".json.tmp").exists()


def test_page_label_fallback_and_public_task_contract(tmp_path) -> None:
    missing_pdf = tmp_path / "missing.pdf"

    assert _page_label_map(missing_pdf, 3) == {1: "1", 2: "2", 3: "3"}
    assert embed_pdf_book.name == "library.embed_pdf_book"
    assert embed_pdf_book_old.name == "library.embed_pdf_book_old"


def test_e5_prefixes_are_applied_once() -> None:
    assert document_embedding_text("rule text") == "passage: rule text"
    assert document_embedding_text("passage: rule text") == "passage: rule text"
    assert query_embedding_text("find a spell") == "query: find a spell"
    assert query_embedding_text("query: find a spell") == "query: find a spell"


def test_parse_quality_summary_reports_explicit_and_observable_gaps() -> None:
    summary = _parse_quality_summary(
        native_page_count=3,
        canonical={"pages": {"1": {}, "2": {}, "3": {}}},
        items=[
            {"block_type": "table", "text": "| A |", "provenance": [{"page_no": 1}]},
            {"block_type": "table", "text": "", "provenance": [{"page_no": 2}]},
            {"block_type": "list", "text": "", "provenance": [{"page_no": 2}]},
            {"block_type": "picture", "text": "", "provenance": [{"page_no": 2}]},
        ],
        diagnostics=["error: failed page 3"],
    )

    assert summary["pages_without_structured_blocks"] == 1
    assert summary["tables_detected"] == 2
    assert summary["tables_without_text"] == 1
    assert summary["lists_without_text"] == 1
    assert summary["explicit_parser_diagnostics"] == 1


class _RecordResult:
    def __init__(self, record):
        self.record = record

    async def single(self):
        return self.record


class _ActivationTx:
    def __init__(self):
        self.calls = []

    async def run(self, query, **params):
        self.calls.append((query, params))
        return _RecordResult({"previous_id": "old-ingestion"})


class _ActivationSession:
    def __init__(self):
        self.tx = _ActivationTx()

    async def execute_write(self, callback):
        return await callback(self.tx)


@pytest.mark.asyncio
async def test_activation_switches_candidate_label_in_one_transaction() -> None:
    session = _ActivationSession()
    service = DoclingIngestionService.__new__(DoclingIngestionService)
    service.graph_session = session

    previous = await service._activate(20, "new-ingestion")

    assert previous == "old-ingestion"
    assert len(session.tx.calls) == 1
    query, params = session.tx.calls[0]
    assert "REMOVE prior:PdfChunk" in query
    assert "SET prior:PdfChunkRetired" in query
    assert "REMOVE candidate:PdfChunkCandidate" in query
    assert "SET candidate:PdfChunk" in query
    assert params == {"library_item_id": 20, "ingestion_id": "new-ingestion"}
