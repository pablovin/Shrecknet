import pytest

from shrecknet_client.models import CharacterAgentQueryRequest
from shrecknet_client.resources import CharacterAgentsAPI


class Client:
    def __init__(self):
        self.call = None

    async def raw_request(self, method, path, **kwargs):
        self.call = (method, path, kwargs)
        if method == "GET":
            return {
                "job_id": 12, "character_agent_id": "character-1",
                "status": "done", "stage": "completed", "progress": 1.0,
                "result": {
                    "type": "text", "content": "I refuse.",
                    "decision_basis": "The refusal fits the request.",
                },
                "error": None, "created_at": "2026-07-27T10:00:00Z",
                "updated_at": "2026-07-27T10:00:01Z",
                "completed_at": "2026-07-27T10:00:01Z",
            }
        return {
            "job_id": 12, "status": "queued", "stage": "queued",
            "progress": 0.0,
            "status_url": "/character-agents/character-1/query-jobs/12",
        }


@pytest.mark.asyncio
async def test_character_agent_query_sdk_contract():
    client = Client()
    queued = await CharacterAgentsAPI(client).query(
        "character-1",
        CharacterAgentQueryRequest(
            query="Reply",
            use_character_identity=False,
        ),
    )
    assert queued.job_id == 12
    assert client.call[0:2] == ("POST", "/character-agents/character-1/query")
    assert client.call[2]["json"]["use_character_identity"] is False
    assert "max_tokens" not in client.call[2]["json"]["generation"]
    assert "mode" not in client.call[2]["json"]["generation"]

    result = await CharacterAgentsAPI(client).get_query_job("character-1", 12)
    assert result.result.content == "I refuse."
    assert client.call[0:2] == (
        "GET", "/character-agents/character-1/query-jobs/12"
    )


def test_character_agent_query_uses_identity_by_default():
    assert CharacterAgentQueryRequest(query="Reply").use_character_identity is True
