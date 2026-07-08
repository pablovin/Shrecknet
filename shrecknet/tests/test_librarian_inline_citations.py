from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.jobs.librarian.librarian import LibrarianOrchestrator
from app.jobs.librarian.schemas import RetrievedChunk


class _NoopLLM:
    async def chat(self, *args, **kwargs):  # pragma: no cover - not used here
        return "ok"


class _NoopPdfEmbeddingService:
    pass


class _CaptureLLM:
    def __init__(self) -> None:
        self.messages = []

    async def chat(self, *args, **kwargs):
        self.messages = kwargs["messages"]
        return (
            '[A test fact]{cite library_item_id=7 '
            'library_item_name="Pendragon Core" page=13}.'
        )


class _PlanningCaptureLLM:
    def __init__(self) -> None:
        self.messages = []

    async def chat(self, *args, **kwargs):
        self.messages = kwargs["messages"]
        return (
            '| Occupation | Details |\n'
            '| --- | --- |\n'
            '| Antiquarian | [Uses Appraise and History]{cite library_item_id=7 '
            'library_item_name="CoC Investigator Rulebook" page=33} |'
        )


class _PlanningPdfEmbeddingService:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.expanded = False

    async def search_chunks(self, **kwargs):
        self.queries.append(kwargs["query_text"])
        idx = len(self.queries)
        return [
            {
                "library_item_id": 7,
                "chunk_index": idx,
                "page_number": 33 + idx,
                "start_page_number": 33 + idx,
                "end_page_number": 33 + idx,
                "page_numbers": [33 + idx],
                "text": f"Occupation row {idx}: Antiquarian Appraise History Library Use",
                "score": 0.9 - idx * 0.01,
                "vector_score": 0.7,
                "fulltext_score": 1.0,
                "pdf_url": "https://example.test/content.pdf",
                "page_url": f"https://example.test/content.pdf#page={33 + idx}",
            }
        ]

    async def fetch_chunks_by_page_anchors(self, **_kwargs):
        return []

    async def expand_chunks_by_page_neighbors(self, chunks, **_kwargs):
        self.expanded = True
        extra = dict(chunks[0])
        extra["chunk_index"] = 99
        extra["page_number"] = 35
        extra["text"] = "Neighbor occupation table continuation"
        extra["score"] = 0.88
        return chunks + [extra]


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


@pytest.mark.asyncio
async def test_generate_answer_prompt_includes_rpg_system() -> None:
    llm = _CaptureLLM()
    orchestrator = LibrarianOrchestrator(
        llm_client=llm,
        pdf_embedding_service=_NoopPdfEmbeddingService(),
    )

    await orchestrator._generate_answer_with_style(
        query="How does a passion roll work?",
        chunks=[
            RetrievedChunk(
                library_item_id=7,
                page_number=13,
                text="Passion rolls can inspire a knight.",
                score=0.91,
                book_title="Pendragon Core",
            )
        ],
        writing_style=None,
        rpg_system="Pendragon",
        trace=[],
    )

    assert "expert on the Pendragon RPG system" in llm.messages[0]["content"]
    assert "expert on the Pendragon RPG system" in llm.messages[1]["content"]


def test_retrieval_planner_detects_exhaustive_table_named_terms_and_pages() -> None:
    orchestrator = LibrarianOrchestrator(
        llm_client=_NoopLLM(),
        pdf_embedding_service=_NoopPdfEmbeddingService(),
    )

    occupations = orchestrator._plan_retrieval(
        "Give me a list of the rulebook occupations",
        "Call of Cthulhu",
    )
    assert occupations.exhaustive is True
    assert occupations.table_like is True
    assert any("occupation table" in query.lower() for query in occupations.subqueries)

    antiquarian = orchestrator._plan_retrieval(
        "Can you explain me the Antiquarian occupation, for example?",
        "Call of Cthulhu",
    )
    assert "Antiquarian" in antiquarian.named_terms
    assert any("Antiquarian" in query for query in antiquarian.subqueries)

    page = orchestrator._plan_retrieval("What about Core p.33?", "Call of Cthulhu")
    assert page.page_anchors == [{"title": "Core", "page": 33}]


@pytest.mark.asyncio
async def test_execute_uses_planned_multi_query_expansion_and_table_prompt() -> None:
    llm = _PlanningCaptureLLM()
    pdf = _PlanningPdfEmbeddingService()
    orchestrator = LibrarianOrchestrator(llm_client=llm, pdf_embedding_service=pdf)

    async def _items(*_args, **_kwargs):
        return [7]

    async def _metadata(*_args, **_kwargs):
        return {7: {"title": "CoC Investigator Rulebook", "authors": "Staff", "vectorized": True}}

    orchestrator._list_vectorized_item_ids = _items
    orchestrator._fetch_library_metadata = _metadata
    agent = SimpleNamespace(
        id="agent-1",
        writing_style=None,
        ontologies=[SimpleNamespace(id=1, rpg_system="Call of Cthulhu")],
    )
    request = SimpleNamespace(
        query="Give me a list of the rulebook occupations",
        top_k=10,
        library_item_ids=None,
        include_trace=True,
        score_threshold=None,
        candidate_limit=None,
        hybrid_rerank=True,
        max_chunks_per_item=None,
        dynamic_score_floor=False,
        mode="both",
    )

    response = await orchestrator.execute(agent, request, db_session=SimpleNamespace())

    assert len(pdf.queries) > 1
    assert pdf.expanded is True
    assert response.subqueries
    assert "| Occupation | Details |" in (response.answer or "")
    user_prompt = llm.messages[1]["content"]
    assert "Markdown tables are allowed" in user_prompt
    assert "Source 6" in user_prompt
