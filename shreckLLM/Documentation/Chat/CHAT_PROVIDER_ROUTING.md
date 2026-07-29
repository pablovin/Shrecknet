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

`GET /status` exposes global `in_flight_requests` and `waiting_requests`, plus
`provider_limiters.<provider_id>.active_requests` and `queue_depth`.
`queue_depth` counts only calls waiting for a provider slot; it is normally
zero while calls execute in parallel below the configured limit.

The runtime `request_timeout_seconds` setting is the authoritative timeout for
one provider attempt across every provider. DeepInfra and OpenRouter do not
have separate hard-coded generation deadlines. Shrecknet callers poll queued
jobs until they succeed or fail and do not apply an additional overall
generation timeout. The Shrecknet HTTP transport timeout only bounds an
individual submit/status/result HTTP operation.

Administrators and world builders can update the timeout without restarting
services:

```http
PUT /config
Content-Type: application/json

{"request_timeout_seconds": 600}
```

`GET /config/schema` exposes this field as frontend-editable with hot change
impact.

shreckLLM is the sole owner of OpenAI-compatible retry behavior. The OpenAI SDK
transport is configured with `max_retries=0`, preventing hidden duplicate
attempts beneath the job-level policy. `chat_job_max_retries` controls the
gateway policy equally for every provider and model; there are no
model-specific retry caps. A value of `0` means one total attempt, `1` means at
most two total attempts, and so on. The job status `retry_count` is updated
before each retry. Only retryable provider overload, timeout, and dependency
failures consume this policy. When a provider overload activates its configured
cooldown, shreckLLM preserves a provider `Retry-After` response when one is
available and otherwise uses the provider's `cooldown_seconds_on_429` limit
(default `10` seconds). The retry delay is never shorter than the remaining
cooldown plus a safety margin and randomized jitter.

The provider cooldown is checked again after the request acquires its
provider-specific semaphore. A request that encounters an active local
cooldown waits while holding that slot; it does not call the provider and does
not consume a configured retry. The safety margin and jitter prevent a group
of concurrent jobs from waking at the same instant and producing another
rate-limit burst. Provider concurrency still defines the maximum number of
simultaneous upstream calls; it does not override an upstream account or model
rate limit.

`GET /config/schema` exposes `chat_job_max_retries` as a hot,
frontend-editable field. Administrators and world builders can update it with:

```http
PUT /config
Content-Type: application/json

{"chat_job_max_retries": 1}
```

DeepInfra chat requests explicitly send
`extra_body={"service_tier": "default"}`. DeepInfra names its normal,
non-priority tier `default`; shreckLLM never requests the optional `priority`
tier and therefore does not opt into its surcharge. Other OpenAI-compatible
providers do not receive this DeepInfra-specific field.

Every shrecknet LLM call uses `ShreckLLMClient`, which submits
`POST /chat/jobs`. Both synchronous `POST /chat` and queued jobs converge on
the same `_execute_chat_request` and `_run_chat` path, so they acquire the
global limiter followed by the selected provider's semaphore. The effective
gateway capacity for one provider is:

`min(max_concurrent_requests, provider_limits[provider_id].max_concurrent)`

Conversation-memory calls may be further serialized by `conversation_id` to
preserve message ordering. Task-local batching may submit fewer calls, but
cannot bypass or raise the gateway provider limit.

At startup, every configured model of every active provider receives a
one-token warm-up request. Warm-ups run concurrently while respecting the
global and per-provider concurrency settings. The provider is marked warmed
only after all its configured model warm-ups succeed.

## Strict v1 rule
`POST /chat` requires explicit `provider_id`.

Supported today:
- `ollama`
- `ollama_cloud`
- `openai`
- `anthropic`
- `deepinfra`
- `openrouter`

Unsupported provider behavior:
- HTTP `400`
- error detail: `unsupported provider_id: <value>`

For `provider_id: "openrouter"`, shreckLLM automatically appends `:nitro` to
the selected model when issuing the provider request. The configured model and
the response's `requested_model`/`resolved_model` remain the catalog model ID.
The suffix is idempotent and requests throughput-based endpoint sorting; it
does not guarantee TPS.

Every OpenRouter request also sends the request's boolean `reasoning` choice in
the provider's native `reasoning` body field. Enabled requests send
`{"enabled": true, "effort": "high"}` so reasoning-capable models use
OpenRouter's high-effort normalization. Disabled requests send
`{"enabled": false}`. The public default is `false`. OpenRouter may translate
`high` to a model's supported effort level or reasoning-token budget; the exact
reasoning behavior and token use remain model-dependent. Provider adapters that
do not support a native reasoning control accept the public flag and ignore it.

## Request schema
```json
{
  "provider_id": "ollama|ollama_cloud|openai|anthropic|deepinfra|openrouter",
  "model": "optional explicit model",
  "reasoning": false,
  "messages": [{"role": "user", "content": "..."}],
  "temperature": 0.7,
  "max_tokens": 512,
  "response_format": {
    "type": "json_schema",
    "json_schema": {"name": "result", "strict": true, "schema": {}}
  },
  "conversation_id": "optional",
  "use_conversation_memory": false,
  "metadata": {}
}
```

OpenAI-compatible providers receive `response_format` unchanged. Ollama receives
the nested JSON Schema through its native `format` field. Providers without
structured-output support reject the option with HTTP 400 so callers can retry
without it.

`reasoning` is valid for every provider and defaults to `false`. It expresses
caller intent without claiming that every provider or model can honor it.
OpenRouter receives its native control with high effort when enabled.
Unsupported adapters ignore the flag rather than rejecting an otherwise valid
request.

## Shared memory across providers
If the same `conversation_id` is used across multiple providers, history remains shared.

## Future providers
New providers can be added by implementing adapter methods and registering them by `provider_id` in the registry.
