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
- expert override fields default to `frontend_editable=false`
- expert override fields default to `derived_from_profile=true`

`default_provider_id` is intentionally hidden from schema/UI.

## Config Endpoints
- `GET /config/schema`
- `GET /config`
- `PUT /config`
- `POST /config/reload`

## Provider Token Endpoints
- `PUT /config/openai-token`
- `DELETE /config/openai-token`
- `PUT /config/anthropic-token`
- `DELETE /config/anthropic-token`
- `PUT /config/ollama-cloud-token`
- `DELETE /config/ollama-cloud-token`

## Provider Validation Endpoints
- `GET /providers/validate`
- `GET /providers/{provider_id}/validate`
- `GET /providers/openai/validate`
- `GET /providers/anthropic/validate`
