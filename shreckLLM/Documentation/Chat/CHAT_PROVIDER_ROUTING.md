# Chat Provider Routing

Chat requests may include an optional positive `max_tokens` value. shreckLLM
forwards it using the selected provider's completion-token parameter. Omitting
the field retains the provider's existing default.

## Concurrent job execution and timing

Submitted chat jobs are processed by a bounded in-process worker pool sized to
the runtime `max_concurrent_requests` setting. The global request limiter and
any configured provider limits remain authoritative, so increasing the worker
pool never exceeds a provider's `max_concurrent` value.

`GET /chat/jobs/{job_id}` reports `queue_wait_ms` after a job starts and
`execution_ms` after it finishes. Service logs additionally report provider
semaphore wait time (`provider_slot_acquired`) and provider request latency.
These values distinguish gateway queueing, provider-specific queueing, and
model execution when investigating a timeout.

## Strict v1 rule
`POST /chat` requires explicit `provider_id`.

Supported today:
- `ollama`
- `openai`

Unsupported provider behavior:
- HTTP `400`
- error detail: `unsupported provider_id: <value>`

## Request schema
```json
{
  "provider_id": "ollama|openai",
  "model": "optional explicit model",
  "messages": [{"role": "user", "content": "..."}],
  "temperature": 0.7,
  "max_tokens": 512,
  "conversation_id": "optional",
  "use_conversation_memory": false,
  "metadata": {}
}
```

## Shared memory across providers
If the same `conversation_id` is used across multiple providers, history remains shared.

## Future providers
New providers can be added by implementing adapter methods and registering them by `provider_id` in the registry.
