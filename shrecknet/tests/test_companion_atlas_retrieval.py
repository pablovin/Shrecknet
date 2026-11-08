"""Atlas retrieval must build a valid elder plan instead of failing validation."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.integrations.companion import retrieval


@pytest.mark.asyncio
async def test_atlas_retrieval_uses_elder_planner_and_returns_evidence(monkeypatch):
    from app.integrations.llm import shreckllm_client
    from app.jobs.elder import planner
    from app.services import elder_retrieval_service

    settings = SimpleNamespace(
        companion_tool_timeout_s=1,
        companion_web_timeout_s=1,
        neo4j_database="neo4j",
        shreckllm_base_url="http://shreckllm",
        model_elder_planner=SimpleNamespace(model_copy=lambda **_: None),
        model_agents_repair_json=SimpleNamespace(model_copy=lambda **_: None),
        companion_rich_response_active=False,
    )
    monkeypatch.setattr(retrieval, "get_settings", lambda: settings)
    monkeypatch.setattr(retrieval, "get_driver", lambda: SimpleNamespace(
        session=lambda **_: _Session()))

    planned = SimpleNamespace(steps=[], terminal_evidence_steps=[])

    async def create_retrieval_plan(**kwargs):
        return planned

    row = SimpleNamespace(
        node_id="node-1", node_label="EntityInstance", node_name="Telmori",
        node_alias=None, text="Telmori are a wolf-people of Glorantha.",
        properties={"updated_at": "2026-01-01"},
    )

    async def retrieve_plan(*args, **kwargs):
        return ({"companion": [row]}, None, None)

    monkeypatch.setattr(planner, "create_retrieval_plan", create_retrieval_plan)
    monkeypatch.setattr(elder_retrieval_service, "retrieve_plan", retrieve_plan)
    monkeypatch.setattr(shreckllm_client, "ShreckLLMClient", _Client)

    adapter = retrieval.CompanionRetrieval(
        SimpleNamespace(request={"context": {"ontology_id": 6}}), None)
    result = await adapter.retrieve("atlas", "Tell me about the Telmori")

    assert result["status"] == "ok"
    assert result["evidence"][0]["source_type"] == "atlas"
    assert result["evidence"][0]["source_id"] == "node-1"


class _Client:
    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Session:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, *args):
        return False
