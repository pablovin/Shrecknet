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
    response = await CharacterAgentsAPI(client).query("character-1", CharacterAgentQueryRequest(query="Reply"))
    assert response.content == "I refuse."
    assert client.call[0:2] == ("POST", "/character-agents/character-1/query")
