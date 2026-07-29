from __future__ import annotations

from app.jobs.elder.schemas import ElderQueryRequest
from app.jobs.librarian.schemas import LibrarianQueryRequest


def test_elder_accepts_long_companion_tool_wrapper_queries() -> None:
    query = "previous agent response " * 300

    request = ElderQueryRequest(query=query)
    assert request.query == query


def test_elder_has_no_global_top_k_result_cap() -> None:
    assert "top_k" not in ElderQueryRequest.model_fields


def test_elder_has_no_caller_controlled_route_or_evidence_budget() -> None:
    assert {"fast", "route", "synthesis_evidence_budget_tokens"}.isdisjoint(
        ElderQueryRequest.model_fields
    )


def test_librarian_accepts_long_companion_tool_wrapper_queries() -> None:
    query = "previous agent response " * 300

    request = LibrarianQueryRequest(query=query)

    assert request.query == query
