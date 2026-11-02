from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from shrecknet_client.models import ElderWorldScope, ElderQueryRequest, ElderChatCreate
from shrecknet_client.resources import ElderAPI


@pytest.mark.asyncio
async def test_builtin_elder_query_supplies_scope_without_an_agent():
    client = SimpleNamespace(
        raw_request=AsyncMock(
            return_value={
                "agent_id": "elder",
                "query": "Compare",
                "answer": "Answer",
                "trace_id": "test",
                "retrieval_plan": {
                    "answer_goal": "Compare",
                    "response_scope": "standard",
                    "steps": [],
                },
            }
        )
    )
    api = ElderAPI(client, None, None, None)
    response = await api.query_site(
        ElderQueryRequest(
            query="Compare",
            world_scope=ElderWorldScope(mode="selected", ontology_ids=[7, 8]),
        )
    )
    assert response.answer == "Answer"
    assert client.raw_request.call_args.args == ("POST", "/jobs/elder/query")
    assert client.raw_request.call_args.kwargs["json"]["world_scope"] == {
        "mode": "selected",
        "ontology_ids": [7, 8],
    }
    assert "agent_id" not in client.raw_request.call_args.kwargs["json"]
    assert (
        ElderChatCreate(name="All", world_scope=ElderWorldScope(mode="all")).agent_id
        is None
    )
