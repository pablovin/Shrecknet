from __future__ import annotations

from app.jobs.elder.schemas import DecomposedIntent, ElderQueryRequest
from app.jobs.librarian.schemas import LibrarianQueryRequest


def test_elder_accepts_long_companion_tool_wrapper_queries() -> None:
    query = "previous agent response " * 300

    request = ElderQueryRequest(query=query)
    intent = DecomposedIntent(subquery=query)

    assert request.query == query
    assert intent.subquery == query


def test_librarian_accepts_long_companion_tool_wrapper_queries() -> None:
    query = "previous agent response " * 300

    request = LibrarianQueryRequest(query=query)

    assert request.query == query
