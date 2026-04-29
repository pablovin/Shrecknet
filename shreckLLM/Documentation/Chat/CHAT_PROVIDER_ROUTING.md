# Chat Provider Routing

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
