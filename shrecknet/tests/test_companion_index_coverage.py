"""ShreckWorld compatibility diagnostics must survive retrieval and answer history."""
from types import SimpleNamespace

import httpx
import pytest

from app.integrations.companion import retrieval
from app.jobs.companion.contracts import build_answer
from app.schemas.character_companion import AnswerDetailsRead


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "coverage, citations, status, reason",
    [
        ({"requested_source_count": 1, "searched_source_count": 1, "sources_without_ready_index": 0, "sources_requiring_reindex": 0}, [], "empty", None),
        ({"requested_source_count": 1, "searched_source_count": 0, "sources_without_ready_index": 0, "sources_requiring_reindex": 1}, [], "unavailable", "incompatible_index"),
        ({"requested_source_count": 2, "searched_source_count": 1, "sources_without_ready_index": 0, "sources_requiring_reindex": 1}, [{"library_item_id": "book", "excerpt": "A rule."}], "unavailable", "incompatible_index"),
        ({"requested_source_count": 1, "searched_source_count": 0, "sources_without_ready_index": 1, "sources_requiring_reindex": 0}, [], "unavailable", "index_not_ready"),
    ],
)
async def test_rules_source_coverage_is_not_mistaken_for_empty(monkeypatch, coverage, citations, status, reason):
    settings = SimpleNamespace(companion_tool_timeout_s=1, companion_shreckworld_url="http://shreckworld")
    monkeypatch.setattr(retrieval, "get_settings", lambda: settings)
    monkeypatch.setattr(retrieval, "grant", lambda *args: "test-grant")
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"citations": citations, "source_coverage": coverage}))
    monkeypatch.setattr(retrieval.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    adapter = retrieval.CompanionRetrieval(SimpleNamespace(request={"context": {"ontology_id": 7}}), None)

    outcome = await adapter.retrieve("shreckworld", "What is the rule?")
    assert outcome["status"] == status
    assert outcome.get("reason") == reason
    assert outcome["source_coverage"] == coverage
    assert len(outcome["evidence"]) == len(citations)
    answer = build_answer("I can only use the available sources.", outcome["evidence"], [outcome], query="What is the rule?")
    details = AnswerDetailsRead.model_validate(answer)
    assert details.tool_calls[0].reason == reason
    assert details.tool_calls[0].source_coverage == coverage
    if reason:
        assert answer["coverage"] == "partial"
        assert reason in answer["warnings"][0]


@pytest.mark.asyncio
async def test_missing_coverage_is_unavailable_not_empty(monkeypatch):
    settings = SimpleNamespace(companion_tool_timeout_s=1, companion_shreckworld_url="http://shreckworld")
    monkeypatch.setattr(retrieval, "get_settings", lambda: settings)
    monkeypatch.setattr(retrieval, "grant", lambda *args: "test-grant")
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"citations": []}))
    monkeypatch.setattr(retrieval.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    adapter = retrieval.CompanionRetrieval(SimpleNamespace(request={"context": {"ontology_id": 7}}), None)
    assert (await adapter.retrieve("shreckworld", "rule"))["status"] == "unavailable"
