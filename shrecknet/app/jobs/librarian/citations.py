"""Trusted Librarian citation extraction and rendering."""

from __future__ import annotations

import re

from app.jobs.librarian.schemas import RetrievedChunk


def extract_sources(answer: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    if not answer:
        return []
    source_ids = set(re.findall(r"\{cite[^}]*source_id\s*=\s*([A-Za-z0-9_-]+)[^}]*\}", answer))
    return [chunk for chunk in chunks if chunk.source_id in source_ids]


def render_inline_citations(answer: str, chunks: list[RetrievedChunk]) -> str:
    if not answer:
        return answer
    source_index = {chunk.source_id: chunk for chunk in chunks if chunk.source_id}
    pattern = re.compile(r"\[(?P<text>.*?)\]\{cite(?P<attrs>[^}]*)\}", re.DOTALL)

    def replace(match: re.Match[str]) -> str:
        text = (match.group("text") or "").strip()
        attrs = match.group("attrs") or ""
        source_match = re.search(r"\bsource_id\s*=\s*([^\s]+)", attrs)
        source_id = source_match.group(1).strip().strip('"') if source_match else ""
        chunk = source_index.get(source_id)
        if chunk is None:
            return text
        title = chunk.book_title or f"Book #{chunk.library_item_id}"
        page = chunk.display_page_label or chunk.page_number
        if chunk.page_url:
            return f"{text} (according to [{title}, p.{page}]({chunk.page_url}))"
        return f"{text} (according to {title}, p.{page})"

    return re.sub(r"\n{3,}", "\n\n", pattern.sub(replace, answer)).strip()
