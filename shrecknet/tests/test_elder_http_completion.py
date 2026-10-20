"""Completion responses and persisted history agree across Elder entry points."""

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.routers import elder, elder_chats
from app.jobs.elder.schemas import ElderQueryResponse, ElderRetrievalPlan
from app.repositories.agent_repository import AgentRepository
from app.repositories.elder_chat_repository import ElderChatRepository
from app.utils import chat_store


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/jobs/elder/elder/query", "/chat/messages/stream"])
async def test_completed_elder_reply_returns_json_and_is_immediately_readable(path, monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    chat = SimpleNamespace(id="chat", agent_id="elder", user_id=1, name="Conversation", color=None, created_at=now, updated_at=now)
    agent = SimpleNamespace(id="elder", active=True, job="elder", ontologies=[])

    async def get_agent(_repo, _agent_id):
        return agent

    async def get_chat(_repo, _chat_id):
        return chat

    response = ElderQueryResponse(
        agent_id="elder", query="Why?", answer="Completed answer¹", trace_id="trace",
        retrieval_plan=ElderRetrievalPlan(answer_goal="Explain", response_scope="standard", steps=[]),
        sources=[{"node_id": "entity", "node_name": "Source", "properties": {"date": "2026-10-20"}}],
        timings={"total_ms": 18000}, llm_usage=[{"stage": "synthesis", "wait_ms": 18000}],
    )

    async def execute(*_args):
        return response

    monkeypatch.setattr(AgentRepository, "get_by_id", get_agent)
    monkeypatch.setattr(ElderChatRepository, "get_by_id", get_chat)
    monkeypatch.setattr(chat_store, "get_settings", lambda: SimpleNamespace(media_root=tmp_path))
    monkeypatch.setattr(elder, "require_ai_agents_enabled", lambda: None)
    app = FastAPI()
    app.include_router(elder.router)
    app.include_router(elder.chat_router)
    app.include_router(elder_chats.router)
    app.dependency_overrides[elder.get_current_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[elder.get_db_session] = lambda: object()
    app.dependency_overrides[elder.get_elder_orchestrator] = lambda: SimpleNamespace(execute=execute)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = {"query": "Why?", "chat_id": "chat"}
        if path.endswith("/stream"):
            payload["agent_id"] = "elder"
        result = await client.post(path, json=payload)
        assert result.status_code == 200, result.text
        assert result.headers["content-type"].startswith("application/json")
        assert result.json()["answer"] == "Completed answer¹"
        saved = await client.get("/jobs/elder/chats/chat?include_history=true")
        assert saved.status_code == 200, saved.text
        messages = saved.json()["history"]
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert messages[-1]["content"] == result.json()["answer"]
        assert messages[-1]["metadata"]["sources"] == result.json()["sources"]
        assert messages[-1]["metadata"]["trace_id"] == "trace"
