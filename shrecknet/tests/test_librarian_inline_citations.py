from __future__ import annotations

from app.jobs.librarian.librarian import LibrarianOrchestrator
from app.jobs.librarian.schemas import RetrievedChunk


class _NoopLLM:
    async def chat(self, *args, **kwargs):  # pragma: no cover - not used here
        return "ok"


class _NoopPdfEmbeddingService:
    pass


def test_render_inline_book_citations_with_page_links() -> None:
    orchestrator = LibrarianOrchestrator(
        llm_client=_NoopLLM(),
        pdf_embedding_service=_NoopPdfEmbeddingService(),
    )

    answer = (
        '[Pregnancy applies a -2 DEX penalty]{cite library_item_id=7 '
        'library_item_name="Pendragon Core" page=13} in standard play.'
    )

    chunks = [
        RetrievedChunk(
            library_item_id=7,
            page_number=13,
            text="Pregnancy applies a -2 DEX penalty.",
            score=0.91,
            pdf_url="https://example.test/library/1/7/content.pdf",
            page_url="https://example.test/library/1/7/content.pdf#page=13",
            book_title="Pendragon Core",
            book_authors="Staff",
        )
    ]

    rendered = orchestrator._render_inline_book_citations(answer, chunks)

    assert "{cite" not in rendered
    assert "according to [Pendragon Core, p.13](https://example.test/library/1/7/content.pdf#page=13)" in rendered
