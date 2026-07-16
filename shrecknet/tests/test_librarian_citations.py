from app.jobs.librarian.citations import extract_sources, render_inline_citations
from app.jobs.librarian.schemas import RetrievedChunk


def test_stable_source_citation_is_extracted_and_rendered() -> None:
    chunk = RetrievedChunk(
        library_item_id=7, page_number=13, text="Fact", score=0.9,
        source_id="source-1", book_title="Pendragon Core",
        page_url="https://example.test/content.pdf#page=13",
    )
    raw = "[A test fact]{cite source_id=source-1}."
    assert extract_sources(raw, [chunk]) == [chunk]
    rendered = render_inline_citations(raw, [chunk])
    assert "{cite" not in rendered
    assert "[Pendragon Core, p.13]" in rendered
