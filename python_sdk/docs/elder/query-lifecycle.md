# Elder Query Lifecycle

Elder is a built-in assistant. Users and agent callers choose worlds; administration retains activation and provider/model settings. New calls do not need an Agent record.

## SDK methods and endpoint coverage

| SDK method | Endpoint |
|---|---|
| `sdk.elder.query_site(request)` | `POST /jobs/elder/query` |
| `sdk.elder.query(agent_id, request)` | `POST /jobs/elder/{agent_id}/query` |
| `sdk.elder.create_chat(payload)` | `POST /jobs/elder/chats/` |
| `sdk.elder.list_chats(...)` | `GET /jobs/elder/chats/` |
| `sdk.elder.get_chat(chat_id, include_history)` | `GET /jobs/elder/chats/{chat_id}` |
| `sdk.elder.update_chat(chat_id, payload)` | `PATCH /jobs/elder/chats/{chat_id}` |
| `sdk.elder.delete_chat(chat_id)` | `DELETE /jobs/elder/chats/{chat_id}` |
| `sdk.elder.get_chat_file(chat_id)` | `GET /jobs/elder/chats/{chat_id}/file` |
| `sdk.elder.preflight(...)` | SDK composite check (llm+agent+embedding) |

## Example

```bash
python python_sdk/examples/07_elder/02_elder_query_lifecycle.py
```

```python
from shrecknet_client.models import ElderWorldScope, ElderQueryRequest, ElderChatCreate
scope = ElderWorldScope(mode="selected", ontology_ids=[7, 8])
chat = await sdk.elder.create_chat(ElderChatCreate(name="Compare worlds", world_scope=scope))
response = await sdk.elder.query_site(ElderQueryRequest(query="What happened to Ernst lately?", chat_id=chat.id))
# Stateless tools can pass world_scope=scope directly instead of chat_id.
# ElderWorldScope(mode="all") resolves all currently available worlds.
```

At present, each terminal planner step selects an evidence type. Shrecknet maps
it to a 12k–100k soft evidence target and hydrates complete sources. This is
scheduled to change in the Elder V3 implementation plan to compact local
hydration and response-scope budgets; it is not an SDK request control.

`response.llm_usage` provides one row per Elder model call. Each row identifies
the stage and resolved model, reports input/output/total tokens, and includes
`wait_ms` for the complete model call. Before completion, server logs expose an
`[ELDER_LLM_REQUEST]` header with a preflight input-token estimate, stage, and
target provider/model.

If the API polling deadline expires, the already-submitted shreckLLM job is not
submitted again. shreckLLM exclusively owns provider retry behavior, avoiding
concurrent duplicate generations and duplicate provider charges.

## Notes

- `SHRECKNET_ELDER_AGENT_ID` is required.
- Preflight validates shreckLLM/provider readiness, elder agent eligibility, and embedding availability.
The current runtime plans retrieval, synthesizes neutral cited claims, then makes
a separate character-composition call. The server appends Unicode superscript
markers in `sources` order and returns display-ready text. Elder V3 will combine
the final answer and character voice into one validated synthesis call while
keeping the endpoint and source-attribution contract stable.

The SDK request model should be updated with the backend's existing optional
`instance_id` field. When V3 ships, it should also expose optional
`response_scope` (`brief`, `standard`, or `deep`); clients that omit it remain
compatible with the `standard` default.

New chat responses expose `agent_id=None`, `scope_mode`, resolved `ontology_ids`,
and `read_only`. Scope is fixed per chat; choose a new chat when changing worlds.
Older Agent methods remain compatibility interfaces. Historical chats remain
readable and do not count against the ten-new-chat quota; the global query rejects
legacy ambiguous context. Deploy the matching Shrecknet database and API first.
See the [backend contract](../../../Documentation/Agents/Elder/Elder.md) for storage,
world maintenance, error codes, and rollback.
