# shreckLLM Config Endpoints

Base URL (docker host): `http://localhost:8111`

Auth for all config endpoints:
- `Authorization: Bearer <shrecknet_token>`
- Required role: `admin` or `world_builder`

## GET /config
Returns current runtime config with masked secrets.

Response example:
```json
{
  "default_provider_id": "ollama",
  "provider_defaults": {
    "ollama": {
      "default_model": "gemma3:4b",
      "base_url": "http://ollama:11434",
      "api_key": null
    },
    "openai": {
      "default_model": "gpt-5-nano",
      "base_url": null,
      "api_key": "sk-...abcd"
    }
  },
  "memory_ttl_seconds": 3600,
  "memory_max_messages": 24,
  "max_concurrent_requests": 8,
  "request_timeout_seconds": 45.0,
  "max_queue_wait_seconds": 10.0
}
```

## PUT /config
Updates runtime config in sqlite and applies immediately.

Request body (partial patch supported):
```json
{
  "default_provider_id": "openai",
  "provider_defaults": {
    "openai": {
      "default_model": "gpt-5-nano",
      "base_url": null,
      "api_key": "sk-..."
    }
  },
  "max_concurrent_requests": 12
}
```

## POST /config/reload
Reloads runtime config from sqlite and reapplies adapters.

Response:
```json
{
  "reloaded": true,
  "config": { "...": "same schema as GET /config" }
}
```
