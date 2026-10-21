# Character Companion SDK

Use `sdk.character_companion` with the existing authenticated async client.
This resource is independent from legacy persona methods.

```python
from uuid import uuid4

api = sdk.character_companion
selection = await api.selection()
selection = await api.select("canonical-character-id", selection.version)
chat = await api.create_chat(selection.character_agent_id, selection.version,
                             idempotency_key=str(uuid4()))
turn = await api.send(chat["id"], "Hello", selection.version,
                      idempotency_key=str(uuid4()))
async for name, event in api.events(turn.turn_id):
    if name == "response.delta":
        print(event.payload["delta"], end="", flush=True)
snapshot = await api.turn(turn.turn_id)
```

Keep an action's UUID key across network retries. A changed payload requires a new
key. `events(after=last_event_id)` resumes without resubmitting; on 410 read the
turn snapshot. Deltas are provisional until completion. Account `events()` also
receives later rapport updates.

Methods cover capabilities, candidates, selection/select/clear, relationships,
rapport/history/reset, chats/create/read/rename/delete/messages, send/turn/cancel,
upload/attachment/attachment_content/delete_attachment and administrator model
configuration read/update. `attachment_content(id)` returns authenticated PNG bytes
for a private image reference in a message manifest.
Selection, rapport, turn and event models are typed Pydantic models. List/chat/image
responses retain the server dictionaries. `CompanionModelConfigUpdate` independently
updates any of the four Companion model roles; it never modifies CharacterAgent roles.

See the [runnable example](../examples/12_character_companion/chat.py),
[HTTP contract](../../Documentation/Agents/Companion/Frontend%20API.md), and
[operations](../../Documentation/Agents/Companion/Companion.md).

Agent `LLMModelTarget` values also accept `web_search_enabled=False`. Set it on
Companion synthesis for gateway-backed OpenRouter retrieval; per-turn web permission
still applies. Unsupported providers/models cannot enable search in the administration
UI. See [shared agent web search](../../Documentation/Architecture/LLM_WEB_SEARCH.md).
