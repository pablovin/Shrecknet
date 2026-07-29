# shreckLLM Config Schema v2 Contract

## Current Runtime Contract
This document reflects the **current** `GET /config/schema` behavior implemented by shreckLLM.

## Groups Returned by `GET /config/schema`

### Provider Assignment
- `id`: `provider_assignment`
- `property`: `runtime`
- `fields`: `provider_defaults`

### Expert Overrides
- `id`: `expert_overrides`
- `property`: `runtime`
- `fields`: `provider_limits`, `memory_ttl_seconds`, `memory_max_messages`, `max_concurrent_requests`, `request_timeout_seconds`, `max_queue_wait_seconds`, `chat_job_queue_max_size`, `chat_job_result_ttl_seconds`, `chat_job_poll_default_interval_ms`, `chat_job_max_retries`

## Field Metadata
- `provider_defaults`: `type=provider_map`, `category=Providers`
- `provider_limits`: `type=object`, `category=Providers`
- `memory_ttl_seconds`: `type=integer`, `category=Memory`
- `memory_max_messages`: `type=integer`, `category=Memory`
- `max_concurrent_requests`: `type=integer`, `category=Concurrency`
- `request_timeout_seconds`: `type=number`, `category=Concurrency`
- `max_queue_wait_seconds`: `type=number`, `category=Concurrency`
- `chat_job_queue_max_size`: `type=integer`, `category=Concurrency`
- `chat_job_result_ttl_seconds`: `type=integer`, `category=Concurrency`
- `chat_job_poll_default_interval_ms`: `type=integer`, `category=Concurrency`
- `chat_job_max_retries`: `type=integer`, `category=Concurrency`

Defaults applied by schema generation:
- all fields default to `change_impact=hot`
- expert override fields default to `frontend_editable=false`, except
  `provider_limits`, `request_timeout_seconds`, and `chat_job_max_retries`;
  provider limits tune enforced concurrency, `request_timeout_seconds` is the
  authoritative timeout for every provider attempt, and
  `chat_job_max_retries` is the model-independent retry count
- expert override fields default to `derived_from_profile=true`
- `request_timeout_seconds` and `chat_job_max_retries` explicitly report
  `derived_from_profile=false`

Provider routing is explicit; shreckLLM does not expose a default provider or default provider model.

## Config Endpoints
- `GET /config/schema`
- `GET /config`
- `PUT /config`
- `POST /config/reload`

`PUT /config` accepts partial updates. For example:

```json
{
  "request_timeout_seconds": 600,
  "chat_job_max_retries": 1
}
```

The change is hot-applied. Shrecknet job clients poll to a terminal state and
do not impose a second overall generation timeout. The retry value applies to
every model without special cases.

## Provider Token Endpoints
- `PUT /config/openai-token`
- `DELETE /config/openai-token`
- `PUT /config/anthropic-token`
- `DELETE /config/anthropic-token`
- `PUT /config/deepinfra-token`
- `DELETE /config/deepinfra-token`
- `PUT /config/openrouter-token`
- `DELETE /config/openrouter-token`
- `GET /providers/openrouter/validate`
- `PUT /config/ollama-cloud-token`
- `DELETE /config/ollama-cloud-token`

## Provider Validation Endpoints
- `GET /providers/validate`
- `GET /providers/{provider_id}/validate`
- `GET /providers/openai/validate`
- `GET /providers/anthropic/validate`

`GET /providers/validate` exposes the persisted aggregate `shreckllm_operational` flag
and `operational_provider_ids`. `shreckllm_operational` is true when at least one provider
state is currently valid and has at least one configured model.

`POST /providers/{provider_id}/test` is the canonical mutating provider functionality
test. It probes the provider, persists the provider validity state, and returns the
updated aggregate operational state.
