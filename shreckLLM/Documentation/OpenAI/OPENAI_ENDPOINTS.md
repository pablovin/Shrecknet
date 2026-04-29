# shreckLLM OpenAI Support

Canonical provider id: `openai`

## Chat via OpenAI provider
Endpoint: `POST /chat`

Request:
```json
{
  "provider_id": "openai",
  "model": "gpt-5-nano",
  "messages": [
    {"role": "user", "content": "Summarize this text"}
  ]
}
```

Response contains execution metadata:
- `provider_id`
- `requested_model`
- `resolved_model`
- `provider_request_id`

## Token storage endpoints
Auth required (`admin` or `world_builder`).

### PUT /config/openai-token
```json
{
  "api_key": "sk-..."
}
```

### DELETE /config/openai-token
Clears stored key.

### GET /providers/openai/validate
Checks token presence + validity.

Response example:
```json
{
  "configured": true,
  "present": true,
  "valid": true,
  "error": null
}
```
