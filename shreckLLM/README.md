# shreckLLM

Standalone LLM gateway service for Shrecknet-compatible chat semantics.

## Architecture
- Internal service intended to be consumed by Shrecknet.
- Runtime/provider settings are persisted in local SQLite:
  - `shreckLLM/databases/shreckllm_config.db`
- Config changes are managed via shreckLLM `/config` endpoints.
- `/config` is authenticated by delegating bearer-token validation to Shrecknet (`/users/me`).

## Features
- FastAPI API (`/health`, `/ready`, `/chat`, `/models`, `/status`, `/config`)
- Multi-provider explicit routing via `provider_id` (`ollama`, `openai`, `anthropic`)
- Redis-backed conversation memory with TTL + trimming
- Bounded concurrent chat-job worker pool, with global and per-provider limits
- Queue, provider-slot, and execution timing telemetry for chat jobs
- Per-conversation in-process locking for ordering consistency

## Chat contract
`POST /chat` requires:
- `provider_id` (explicit, no auto mode)
- optional `model`
- `messages`, `temperature`, `max_tokens`
- `conversation_id`, `use_conversation_memory`
- `metadata`

Response includes execution details:
- `provider_id`
- `requested_model`
- `resolved_model`
- `provider_request_id`

## Runtime config model
- `provider_defaults` map keyed by provider id (`ollama`, `openai`, `anthropic`)
- runtime limits and timeouts

## Frontend config endpoints
- `GET /config` (requires bearer token; role `admin` or `world_builder`)
- `PUT /config` (requires bearer token; role `admin` or `world_builder`)
- `POST /config/reload` (requires bearer token; role `admin` or `world_builder`)
- `PUT /config/openai-token` (requires bearer token; role `admin` or `world_builder`)
- `DELETE /config/openai-token` (requires bearer token; role `admin` or `world_builder`)
- `GET /providers/openai/validate` (requires bearer token; role `admin` or `world_builder`)
- `PUT /config/anthropic-token` (requires bearer token; role `admin` or `world_builder`)
- `DELETE /config/anthropic-token` (requires bearer token; role `admin` or `world_builder`)
- `GET /providers/anthropic/validate` (requires bearer token; role `admin` or `world_builder`)

## Anthropic defaults
- Provider id: `anthropic`
- Default model: `claude-3-haiku-20240307`
- Preconfigured models:
  - `claude-3-haiku-20240307`
  - `claude-opus-4-1-20250805`

When running with Docker compose, shreckLLM is exposed on `http://localhost:8111`.
The Docker Compose stack does not start Ollama; the local `ollama` provider defaults to an Ollama API already running on the host at `http://host.docker.internal:11434`.

## Documentation
- `shreckLLM/Documentation/Config/CONFIG_ENDPOINTS.md`
- `shreckLLM/Documentation/LocalLLM/LOCAL_LLM_ENDPOINTS.md`
- `shreckLLM/Documentation/OpenAI/OPENAI_ENDPOINTS.md`
- `shreckLLM/Documentation/Chat/CHAT_PROVIDER_ROUTING.md`

## Run locally
```bash
cd shreckLLM
pip install -e ".[test]"
uvicorn app.main:app --host 0.0.0.0 --port 8110
```

## Smoke test
```bash
cd shreckLLM
python scripts/chat_smoke.py --base-url http://localhost:8110 --provider-id ollama --conversation-id demo-1
```

## Interactive chat
```bash
cd shreckLLM
python scripts/chat_cli.py --base-url http://localhost:8110
```

## Run tests
```bash
cd shreckLLM
pytest
```
