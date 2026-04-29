# shreckLLM Local LLM (Ollama)

Canonical provider id: `ollama`

## Chat via local provider
Endpoint: `POST /chat`

Request:
```json
{
  "provider_id": "ollama",
  "model": "gemma3:4b",
  "messages": [
    {"role": "user", "content": "Hello"}
  ],
  "conversation_id": "chat-123",
  "use_conversation_memory": true
}
```

Response:
```json
{
  "text": "Hi!",
  "provider_id": "ollama",
  "requested_model": "gemma3:4b",
  "resolved_model": "gemma3:4b",
  "provider_request_id": null,
  "model": "gemma3:4b",
  "usage": {
    "prompt_tokens": 42,
    "completion_tokens": 12,
    "total_tokens": 54
  },
  "latency_ms": 312.5,
  "conversation_id": "chat-123",
  "memory_applied": true,
  "metadata": null
}
```

## Provider model catalog
`GET /models` includes:
```json
{
  "providers": {
    "ollama": {
      "default_model": "gemma3:4b",
      "models": ["gemma3:4b", "llama3.1:8b"]
    }
  }
}
```
