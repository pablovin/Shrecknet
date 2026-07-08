# Shrecknet

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688)
![Neo4j](https://img.shields.io/badge/Neo4j-5.x-018BFF)
![Redis](https://img.shields.io/badge/Redis-7.x-DC382D)
![License](https://img.shields.io/badge/license-GPLv3-blue)
![Version](https://img.shields.io/badge/version-0.5.7-orange)

Shrecknet is a scene-centric memory engine for storytelling systems. It builds longitudinal narrative memory where scenes, milestones, ontology structure, and retrieval context evolve together instead of living as disconnected text fragments.

## What Shrecknet Is

Shrecknet is designed for teams building story-aware assistants, campaign memory systems, or world models that must preserve chronology and narrative causality.

Core value:
- Scene and milestone memory as first-class temporal records.
- Structured ontology + graph state + retrieval-ready knowledge in one system.
- Agent workflows (Elder, Librarian, Architect, Novelist) over the same canonical memory.

Architecture overview:
- [Documentation/Architecture/SHRECKNET_ARCHITECTURE.md](Documentation/Architecture/SHRECKNET_ARCHITECTURE.md)
- [Documentation/Architecture/assets/shrecknet-architecture.png](Documentation/Architecture/assets/shrecknet-architecture.png)

## How It Thinks (Scene-Centric)

Shrecknet uses Scene and Milestone as the canonical temporal write model:
- Scene: bounded narrative unit in an ontology instance.
- Milestone: timeline anchor and progression marker within and across scenes.
- Provenance links connect scene evidence back to ontology entities and relationships.

Start here:
- [Documentation/SceneCentricMemory/Data Structure/Data Structure.md](Documentation/SceneCentricMemory/Data Structure/Data Structure.md)
- [Documentation/SceneCentricMemory/Data Structure/Data Structure - ENDPOINTS.md](Documentation/SceneCentricMemory/Data Structure/Data Structure - ENDPOINTS.md)
- [Documentation/SceneCentricMemory/Retrieval/Retrieval.md](Documentation/SceneCentricMemory/Retrieval/Retrieval.md)
- [Documentation/SceneCentricMemory/Retrieval/Retrieval - Endpoints.md](Documentation/SceneCentricMemory/Retrieval/Retrieval - Endpoints.md)
- [Documentation/SceneCentricMemory/Embedding/SCENE_EMBEDDING.md](Documentation/SceneCentricMemory/Embedding/SCENE_EMBEDDING.md)
- [Documentation/SceneCentricMemory/Embedding/FRONTEND_ENDPOINTS_JOBS_SUMMARY.md](Documentation/SceneCentricMemory/Embedding/FRONTEND_ENDPOINTS_JOBS_SUMMARY.md)

## Agents at a Glance

### Elder
Conversational memory and retrieval orchestrator. Elder decomposes questions, retrieves graph and knowledge context, and returns grounded answers.
- [Documentation/Agents/Elder/Elder.md](Documentation/Agents/Elder/Elder.md)

### Librarian
Document intelligence and citation-oriented retrieval over library/PDF embeddings.
- [Documentation/Agents/Librarian/Librarian.MD](Documentation/Agents/Librarian/Librarian.MD)

### Architect
Ontology and narrative-structure evolution agent for keeping world schema and memory coherent over time.
- [Documentation/Agents/Architect/Architect.md](Documentation/Agents/Architect/Architect.md)
- [Documentation/Agents/Architect/Architect - Endpoints.md](Documentation/Agents/Architect/Architect - Endpoints.md)
- [Documentation/Agents/Architect/Analyse/Scene Chunking.md](Documentation/Agents/Architect/Analyse/Scene Chunking.md)

### Novelist
Narrative generation agent for converting notes/session material into polished story output.
- [Documentation/Agents/Novelist/Novelist.md](Documentation/Agents/Novelist/Novelist.md)
- [Documentation/Agents/Novelist.md](Documentation/Agents/Novelist.md)

## Quick Start (Compose Only)

### 1. Prerequisites
- Docker Engine with Docker Compose v2

### 2. Review first-start config

Initial configuration lives in the root `configs/` folder:
- [configs/shrecknet.initial.json](configs/shrecknet.initial.json): Shrecknet first-start settings.
- [configs/shreckllm.initial.json](configs/shreckllm.initial.json): shreckLLM first-start settings.
- [configs/neo4j.env](configs/neo4j.env): Neo4j startup settings that must exist before the API can boot.
- [configs/compose.env](configs/compose.env): Docker Compose operational settings.

Credentials are intentionally empty in the JSON seed files. API keys and provider credentials should be configured after startup through the config APIs so they are stored in the runtime config database.

### 3. Start Ollama separately

Shrecknet no longer starts Ollama inside Docker Compose. Start Ollama on the host before using local LLM models:

```bash
ollama serve
```

The Shrecknet Docker stack expects the host Ollama API at `http://localhost:11434`, exposed to containers as `http://host.docker.internal:11434`. Pull the configured local model separately if needed, for example:

```bash
ollama pull gemma4:e2b
```

For existing installs, the shreckLLM runtime config database is the source of truth after first boot. If you use a different external Ollama endpoint, update the `ollama` provider `base_url` through the shreckLLM `/config` API.

### 4. Start Shrecknet services

```bash
docker compose --env-file configs/compose.env --env-file configs/neo4j.env up --build
```

Shortcut launchers:

- `./run.sh` (Linux/macOS)
- `run.bat` (Windows)

### 5. Verify

```bash
curl -s http://localhost:11434/api/tags
curl -s http://localhost:8100/health
curl -s http://localhost:8100/openapi.json | head
```

Open in browser:
- API docs: `http://localhost:8100/docs`
- Neo4j Browser: `http://localhost:7475`
- Neo4j Bolt: `bolt://localhost:7688`

## What to Expect After Startup

Immediately available:
- API and OpenAPI docs on port `8100`.
- Scene-centric data model endpoints and CRUD flows.
- Neo4j + Redis + worker-backed background jobs.

Requires additional setup:
- LLM provider credentials should be added through the config APIs after startup.
- Local Ollama models must be served outside Docker Compose and reachable from the host on port `11434`, unless you configure a different `ollama` provider `base_url`.
- The initial JSON files seed only missing config DB values. Once `shrecknet_config.db` or `shreckllm_config.db` exists, those databases are the runtime source of truth.
- Delete the relevant config DB only when you intentionally want to reseed from `configs/*.initial.json`.
- `configs/neo4j.env` is the initial Neo4j source of truth for both the Neo4j container and Shrecknet's graph connection. It ships with a local password; change it before first run for non-local use.
- OpenAI and Anthropic credentials belong to shreckLLM provider config, not Shrecknet. Add them through shreckLLM `/config/openai-token` and `/config/anthropic-token` after startup.

Primary references:
- [Documentation/README.md](Documentation/README.md)
- [Documentation/Architecture/SHRECKNET_ARCHITECTURE.md](Documentation/Architecture/SHRECKNET_ARCHITECTURE.md)

## Documentation Map

General index:
- [Documentation/README.md](Documentation/README.md)

Architecture:
- [Documentation/Architecture/SHRECKNET_ARCHITECTURE.md](Documentation/Architecture/SHRECKNET_ARCHITECTURE.md)

Scene-centric memory:
- [Documentation/SceneCentricMemory/Data Structure/Data Structure.md](Documentation/SceneCentricMemory/Data Structure/Data Structure.md)
- [Documentation/SceneCentricMemory/Data Structure/Data Structure - ENDPOINTS.md](Documentation/SceneCentricMemory/Data Structure/Data Structure - ENDPOINTS.md)
- [Documentation/SceneCentricMemory/Retrieval/Retrieval.md](Documentation/SceneCentricMemory/Retrieval/Retrieval.md)
- [Documentation/SceneCentricMemory/Retrieval/Retrieval - Endpoints.md](Documentation/SceneCentricMemory/Retrieval/Retrieval - Endpoints.md)
- [Documentation/SceneCentricMemory/Embedding/SCENE_EMBEDDING.md](Documentation/SceneCentricMemory/Embedding/SCENE_EMBEDDING.md)
- [Documentation/SceneCentricMemory/Embedding/FRONTEND_ENDPOINTS_JOBS_SUMMARY.md](Documentation/SceneCentricMemory/Embedding/FRONTEND_ENDPOINTS_JOBS_SUMMARY.md)

Agents:
- [Documentation/Agents/Elder/Elder.md](Documentation/Agents/Elder/Elder.md)
- [Documentation/Agents/Librarian/Librarian.MD](Documentation/Agents/Librarian/Librarian.MD)
- [Documentation/Agents/Architect/Architect.md](Documentation/Agents/Architect/Architect.md)
- [Documentation/Agents/Architect/Architect - Endpoints.md](Documentation/Agents/Architect/Architect - Endpoints.md)
- [Documentation/Agents/Architect/Analyse/Scene Chunking.md](Documentation/Agents/Architect/Analyse/Scene Chunking.md)
- [Documentation/Agents/Novelist/Novelist.md](Documentation/Agents/Novelist/Novelist.md)
- [Documentation/Agents/Novelist.md](Documentation/Agents/Novelist.md)

## Examples

README keeps only a smoke test. For full workflows, use canonical docs above.

### Smoke test (health + auth reachability)

```bash
# Health
curl -s http://localhost:8100/health

# Register first user (becomes admin if DB is empty)
curl -s -X POST http://localhost:8100/users/ \
  -H "Content-Type: application/json" \
  -d '{
    "username":"keeper",
    "full_name":"World Keeper",
    "email":"keeper@example.com",
    "timezone":"UTC",
    "role":"admin",
    "password":"change-me-strong",
    "entity_ids":[]
  }'

# Login
curl -s -X POST http://localhost:8100/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username":"keeper","password":"change-me-strong"}'
```
