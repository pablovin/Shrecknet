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

## Notes

- `SHRECKNET_ELDER_AGENT_ID` is required.
- Preflight validates shreckLLM/provider readiness, elder agent eligibility, and embedding availability.
