# Elder Query Lifecycle

This phase adds Elder query and chat lifecycle support.

## SDK methods and endpoint coverage

| SDK method | Endpoint |
|---|---|
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
request = ElderQueryRequest(query="What happened to Ernst lately?")
response = await sdk.elder.query(agent_id, request)
```

Each terminal planner step selects an evidence type. Shrecknet maps that type to
a fixed 12k–100k soft evidence target. The complete source crossing a target is
included without truncation, then collection for that step stops.

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
Elder detects the original query language during retrieval planning. Grounded
synthesis then produces atomic neutral English claims with evidence attribution.
A separate character-composition call receives only the original query, detected
language, agent name/description/style, and citation-free claim text. It may
reorder, combine, and condense claims into cohesive passages, but every claim
must be associated exactly once. The server appends Unicode superscript markers
in `sources` order and returns display-ready text; complete evidence attribution
remains in the structured `sources` records. It falls back to the neutral answer
if character rendering fails validation.
