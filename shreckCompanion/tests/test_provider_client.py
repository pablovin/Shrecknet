from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.integrations.clients import ShrecknetProviderClient


@pytest.mark.asyncio
async def test_allocate_tools_uses_shrecknet_agents_routes():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/agents/" and request.url.params.get("job") == "elder":
            assert request.headers["authorization"] == "Bearer user-token"
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "elder-1",
                        "name": "Elder",
                        "job": "elder",
                        "active": True,
                        "ontology_ids": [1],
                        "created_at": "2026-01-01T00:00:00",
                        "updated_at": "2026-01-01T00:00:00",
                    }
                ],
            )
        if request.url.path == "/agents/" and request.url.params.get("job") == "librarian":
            assert request.headers["authorization"] == "Bearer user-token"
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "librarian-1",
                        "name": "Librarian",
                        "job": "librarian",
                        "active": True,
                        "ontology_ids": [1],
                        "created_at": "2026-01-01T00:00:00",
                        "updated_at": "2026-01-01T00:00:00",
                    }
                ],
            )
        return httpx.Response(500, json={"detail": "unexpected path"})

    client = ShrecknetProviderClient(Settings(shrecknet_api_base_url="http://shrecknet.test"))
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="http://shrecknet.test",
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )

    allocation = await client.allocate_tools(user_id=12, ontology_id=1, auth_header="Bearer user-token")
    await client.aclose()

    assert allocation.elder[0].id == "elder-1"
    assert allocation.librarian[0].id == "librarian-1"
    assert any("/agents/?job=elder" in request for request in requests)
    assert any("/agents/?job=librarian" in request for request in requests)
    assert not any("/internal/companion" in request for request in requests)


@pytest.mark.asyncio
async def test_run_elder_uses_jobs_elder_query_route():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/jobs/elder/elder-1/query":
            assert request.headers["authorization"] == "Bearer user-token"
            assert json.loads(request.content) == {"query": "Who is Shrek?", "mode": "both"}
            return httpx.Response(
                200,
                json={
                    "agent_id": "elder-1",
                    "query": "Who is Shrek?",
                    "answer": "A grounded answer.",
                    "sources": [],
                    "trace_id": "trace-1",
                },
            )
        return httpx.Response(500, json={"detail": "unexpected path"})

    client = ShrecknetProviderClient(Settings(shrecknet_api_base_url="http://shrecknet.test"))
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="http://shrecknet.test",
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )

    response = await client.run_elder(
        user_id=12,
        agent_id="elder-1",
        query="Who is Shrek?",
        ontology_id=1,
        auth_header="Bearer user-token",
    )
    await client.aclose()

    assert response["answer"] == "A grounded answer."
    assert any("/jobs/elder/elder-1/query" in request for request in requests)
    assert not any("/internal/companion" in request for request in requests)


@pytest.mark.asyncio
async def test_run_librarian_uses_jobs_librarian_query_route():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/jobs/librarian/librarian-1/query":
            assert request.headers["authorization"] == "Bearer user-token"
            assert json.loads(request.content) == {"query": "What rule applies?", "mode": "both"}
            return httpx.Response(
                200,
                json={
                    "agent_id": "librarian-1",
                    "query": "What rule applies?",
                    "answer": "A rules answer.",
                    "chunks": [],
                    "sources_used": [],
                },
            )
        return httpx.Response(500, json={"detail": "unexpected path"})

    client = ShrecknetProviderClient(Settings(shrecknet_api_base_url="http://shrecknet.test"))
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="http://shrecknet.test",
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )

    response = await client.run_librarian(
        user_id=12,
        agent_id="librarian-1",
        query="What rule applies?",
        ontology_id=1,
        auth_header="Bearer user-token",
    )
    await client.aclose()

    assert response["answer"] == "A rules answer."
    assert any("/jobs/librarian/librarian-1/query" in request for request in requests)
    assert not any("/internal/companion" in request for request in requests)
