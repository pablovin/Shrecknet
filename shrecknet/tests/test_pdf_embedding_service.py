from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.services.pdf_embedding_service import PdfEmbeddingService
class _FakeEmbeddingService:
    model_id = "test-model"
    embed_dim = 3
    chunk_size = 80
    chunk_overlap = 20

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]

    def _chunk_text(
        self, text: str, size: int | None = None, overlap: int | None = None
    ) -> list[str]:
        chunk_size = size or self.chunk_size
        chunk_overlap = overlap or self.chunk_overlap
        chunks: list[str] = []
        i = 0
        while i < len(text):
            end = min(len(text), i + chunk_size)
            chunks.append(text[i:end].strip())
            if end >= len(text):
                break
            i = max(end - chunk_overlap, 0)
        return [chunk for chunk in chunks if chunk]


class _FakeResult:
    def __init__(self, record: dict[str, int] | None = None) -> None:
        self._record = record or {"count": 1}

    async def single(self) -> dict[str, int]:
        return self._record

    def __aiter__(self):
        async def _empty():
            if False:
                yield None
        return _empty()


class _FakeGraphSession:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.rows = rows or []

    async def run(self, *_args, **_kwargs) -> _FakeResult:
        class _IterResult(_FakeResult):
            def __init__(self, rows):
                super().__init__({"count": 1})
                self._rows = rows

            def __aiter__(self):
                async def _gen():
                    for row in self._rows:
                        yield row
                return _gen()

        return _IterResult(self.rows)


class _ProbePdfEmbeddingService(PdfEmbeddingService):
    def __init__(self) -> None:
        super().__init__(_FakeGraphSession(), embedding_service=_FakeEmbeddingService())
        self.embedded_chunks: list[dict[str, object]] = []

    async def _embed_chunks_batch(self, chunks: list[dict[str, object]]) -> None:
        self.embedded_chunks.extend(chunks)


class _FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self, _mode: str) -> str:
        return self._text


class _FakeFitzDoc:
    def __init__(self, pages: list[str], labels: list[str | None] | None = None) -> None:
        self._pages = [_FakePage(text) for text in pages]
        self._labels = labels or [None] * len(pages)

    def __len__(self) -> int:
        return len(self._pages)

    def __getitem__(self, idx: int) -> _FakePage:
        return self._pages[idx]

    def get_page_labels(self) -> list[str | None]:
        return self._labels

    def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_embed_pdf_book_skips_cover_and_builds_semantic_chunks(
    monkeypatch, tmp_path
) -> None:
    pdf_path = tmp_path / "content.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test\n")

    pages = [
        "Cover page title only",
        "Shared Header\n"
        "Page one contains enough body text to be retained after normalization. "
        "It introduces the first concept in detail.\n"
        "15\n",
        "Shared Header\n"
        "Page two continues the same section with additional meaningful material "
        "so the chunk spans multiple pages and keeps strong retrieval context.\n"
        "15\n",
        "Shared Header\n"
        "Page three starts a new section with more relevant narrative so a second "
        "semantic chunk is produced for retrieval.\n"
        "15\n",
    ]

    monkeypatch.setitem(
        sys.modules,
        "fitz",
        SimpleNamespace(open=lambda _path: _FakeFitzDoc(pages)),
    )

    service = _ProbePdfEmbeddingService()
    result = await service.embed_pdf_book(
        library_item_id=7,
        ontology_id=9,
        pdf_path=pdf_path,
        batch_size=2,
    )

    assert result["chunks_created"] >= 2
    assert result["pages_skipped_as_cover"] == 1
    assert result["ocr_pages"] == 0
    assert service.embedded_chunks

    first_chunk = service.embedded_chunks[0]
    assert first_chunk["page_number"] == 2
    assert first_chunk["start_page_number"] == 2
    assert first_chunk["end_page_number"] >= 3
    assert first_chunk["page_numbers"][:2] == [2, 3]
    assert "Shared Header" not in str(first_chunk["text"])
    assert "Cover page" not in str(first_chunk["text"])


@pytest.mark.asyncio
async def test_embed_pdf_book_marks_low_text_documents_as_needing_ocr(
    monkeypatch, tmp_path
) -> None:
    pdf_path = tmp_path / "content.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test\n")

    monkeypatch.setitem(
        sys.modules,
        "fitz",
        SimpleNamespace(open=lambda _path: _FakeFitzDoc(["Cover", "", "   "])),
    )

    service = _ProbePdfEmbeddingService()
    result = await service.embed_pdf_book(
        library_item_id=1,
        ontology_id=1,
        pdf_path=pdf_path,
        batch_size=10,
    )

    assert result["chunks_created"] == 0
    assert result["status"] == "needs_ocr"
    assert result["pages_extracted"] == 0
    assert service.embedded_chunks == []


def test_rerank_hybrid_promotes_lexical_match_and_applies_diversity_cap() -> None:
    service = PdfEmbeddingService(_FakeGraphSession(), embedding_service=_FakeEmbeddingService())
    chunks = [
        {"library_item_id": 1, "chunk_index": 1, "text": "alpha beta lore", "vector_score": 0.70, "page_number": 1},
        {"library_item_id": 1, "chunk_index": 2, "text": "irrelevant text", "vector_score": 0.80, "page_number": 2},
        {"library_item_id": 2, "chunk_index": 1, "text": "alpha beta alpha", "vector_score": 0.69, "page_number": 8},
    ]
    out = service._rerank_and_select_chunks(
        query_text="alpha beta",
        chunks=chunks,
        top_k=2,
        score_threshold=0.0,
        hybrid_rerank=True,
        max_chunks_per_item=1,
        dynamic_score_floor=False,
    )
    assert len(out) == 2
    assert out[0]["library_item_id"] in (1, 2)
    assert out[1]["library_item_id"] in (1, 2)
    assert out[0]["library_item_id"] != out[1]["library_item_id"]
