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


## Chat session limits and context

Each user can keep ten non-deleted Companion chats across all characters. Existing
accounts above ten retain their history; only creation is blocked. Chat creation
serializes the quota check and insertion across workers. An exact idempotency
replay returns the saved chat even at the limit; a new eleventh chat returns
`409` with `detail.code: chat_limit_reached`. Chat titles are trimmed and must
contain 1–255 characters. Duplicate titles are allowed.

`GET /chats` retains `items` and `next_cursor` and adds
`usage: {count, limit, remaining}`. Usage always counts all characters, including
when `character_agent_id` filters the list. `GET /capabilities` adds
`chat_sessions: {max_chats: 10, limit_scope: "user"}`. SDK `chats()` returns these
fields without transformation. All existing routes and SDK methods remain valid.

Delete returns 202 after logical deletion commits and frees one slot. Physical
purge continues through the existing outbox. A running turn returns
`409 chat_busy`; cancel it before retrying deletion. Admission and logical deletion
use conditional chat writes so a deleted chat cannot accept a new turn. Deleted
chat content is excluded from account event replay. `chat.created`, `chat.updated`
and `chat.deleted` account events invalidate session lists; deletion events contain
only the chat ID and have no chat-scoped content. Rapport and character memory
are independent of chat deletion.

Conversation context loads only completed, committed exchanges for the addressed
owner/chat. It includes at most six recent complete exchanges and 48,000 text
characters, then drops complete oldest exchanges as needed for the existing
synthesis prompt budget. It never slices individual messages or includes failed
assistant drafts. New user input enters the prompt separately, exactly once.
Older history remains available in message pagination; automatic summarization
is not implemented. Canonical character identity, pair rapport and character memory
remain separate inputs, so starting a new chat does not reset those inputs.

Explicit per-turn `context` takes precedence over `default_scope`; null/omission
falls back to default scope, then global. `{surface: "global"}` explicitly disables
active-page grounding while retaining character identity and authorized retrieval.
Page IDs never grant access. Source authorization and retrieval policies remain
unchanged; this does not introduce retroactive redaction of historical text.

Deploy the backend before the updated frontend. No database migration is required.
Rolling the frontend back retains chats and backend quota enforcement; removing
quota enforcement requires a backend rollback. Never delete excess legacy chats.
