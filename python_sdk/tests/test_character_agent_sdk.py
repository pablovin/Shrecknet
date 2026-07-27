import pytest

from shrecknet_client.models import CharacterAgentQueryRequest
from shrecknet_client.resources import CharacterAgentsAPI


class Client:
    def __init__(self):
        self.call = None

    async def raw_request(self, method, path, **kwargs):
        self.call = (method, path, kwargs)
        return {"type": "text", "content": "I refuse."}


@pytest.mark.asyncio
async def test_character_agent_query_sdk_contract():
    client = Client()
    response = await CharacterAgentsAPI(client).query(
        "character-1",
        CharacterAgentQueryRequest(
            query="Reply",
            use_character_identity=False,
        ),
    )
    assert response.content == "I refuse."
    assert client.call[0:2] == ("POST", "/character-agents/character-1/query")
    assert client.call[2]["json"]["use_character_identity"] is False
    assert "max_tokens" not in client.call[2]["json"]["generation"]
    assert "mode" not in client.call[2]["json"]["generation"]


def test_character_agent_query_uses_identity_by_default():
    assert CharacterAgentQueryRequest(query="Reply").use_character_identity is True
