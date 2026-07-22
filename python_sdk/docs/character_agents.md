# CharacterAgent queries

```python
from shrecknet_client import Shrecknet
from shrecknet_client.models import CharacterAgentQueryRequest

async with Shrecknet(token="...") as sdk:
    response = await sdk.character_agents.query(
        "character-agent-id",
        CharacterAgentQueryRequest(
            query="Write a brief in-character reply to the accusation."
        ),
    )
    print(response.content)
```

Use `response_format.type="json"` with a caller JSON Schema for structured
content. The API remains admin-only in Phase 1.
