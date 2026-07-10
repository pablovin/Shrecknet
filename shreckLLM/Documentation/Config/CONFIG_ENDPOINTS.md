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
  "provider_defaults": {
    "ollama": {
      "models": ["gemma3:4b"],
      "base_url": "http://host.docker.internal:11434",
      "api_key": null
    },
    "openai": {
      "models": ["gpt-5-nano"],
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
  "provider_defaults": {
    "openai": {
      "models": ["gpt-5-nano"],
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
