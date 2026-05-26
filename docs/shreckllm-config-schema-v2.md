# shreckLLM Config Schema v2 Contract

## Frontend Instructions
1. Call `GET /config/schema` and render `groups`.
2. Use `field_meta` per field (`type`, `help`, `change_impact`, `frontend_editable`).
3. Hide or disable fields where `frontend_editable=false`.
4. Badge fields by `change_impact` (`hot`, `service_restart`, `locked`).
5. Save only editable changed fields to `PUT /config`.
6. Use dedicated token endpoints for provider API keys.

## Groups Returned by `GET /config/schema`

### Providers
- `id`: `providers`
- `property`: `runtime`
- `fields`: `provider_defaults`, `provider_limits`

### Memory
- `id`: `memory`
- `property`: `runtime`
- `fields`: `memory_ttl_seconds`, `memory_max_messages`

### Concurrency
- `id`: `concurrency`
- `property`: `runtime`
- `fields`: `max_concurrent_requests`, `request_timeout_seconds`, `max_queue_wait_seconds`

## Field Metadata
- `provider_defaults`: `type=provider_map`, `category=Providers`
- `provider_limits`: `type=object`, `category=Providers`
- `memory_ttl_seconds`: `type=integer`, `category=Memory`
- `memory_max_messages`: `type=integer`, `category=Memory`
- `max_concurrent_requests`: `type=integer`, `category=Concurrency`
- `request_timeout_seconds`: `type=number`, `category=Concurrency`
- `max_queue_wait_seconds`: `type=number`, `category=Concurrency`

All fields currently default to:
- `change_impact`: `hot`
- `frontend_editable`: `true`

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
