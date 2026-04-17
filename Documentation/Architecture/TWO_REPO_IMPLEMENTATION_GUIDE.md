# Two-Repo Implementation Guide

This repository now contains two independent service roots:

- `shrecknet/`: core platform (users, worlds, ontologies, graph, agents, media, jobs)
- `shreckrpg/`: RPG extension (games, chronicles, activities, favorite pages, alerts, RPG media)

## What Was Implemented

- Separate FastAPI applications with independent dependency manifests
- Domain-specific API surfaces:
  - Shrecknet: `/v1/auth`, `/v1/users`, `/v1/worlds`, `/v1/ontologies`, `/v1/agents`, `/v1/jobs`, `/v1/media`, `/v1/events`
  - ShreckRPG: `/v1/games`, `/v1/chronicles`, `/v1/activities`, `/v1/favorite-pages`, `/v1/alerts`, `/v1/media`, `/v1/integrations/events`
- JWT identity model with JWKS endpoint in Shrecknet and JWKS verification in ShreckRPG
- ShreckRPG integration client with retry + circuit breaker for Shrecknet calls
- Event contract + idempotent event ingestion path for projection updates
- Split migration/export tooling by service ownership, with Shrecknet logic integrated into `shrecknet/app/services/*` and ShreckRPG tooling kept in its own service root
- Bootstrap script for turning folders into independent git repositories:
  - `scripts/bootstrap_two_repos.sh`
- Phase 2 persistence foundation:
  - `shrecknet`: SQL-backed ownership for users/worlds/ontologies/agents/jobs/media.
  - `shreckrpg`: SQL-backed ownership for games/chronicles/activities/favorite pages/alerts/chronicle media.
  - Persistent event-idempotency table (`processed_events`) and projection cache table (`projection_cache`) in ShreckRPG.
- Phase 3 migration pipeline foundation:
  - Deterministic table export/import logic split by service ownership.
  - Replay-safe importers backed by `migration_runs` + `id_mappings` tables.
  - Import verification embedded in the active importer flow.
  - Unified runner script: `scripts/run_phase3_pipeline.sh`.
- Phase 4 extraction wave (bridge):
  - Endpoint ownership matrix documented in `Documentation/Architecture/PHASE4_ENDPOINT_OWNERSHIP.md`.
  - Shrecknet contract endpoints for cross-domain data (`/v1/contracts/users/*`, `/v1/contracts/worlds/*`).
  - ShreckRPG consumes Shrecknet via contracts (no direct cross-domain DB assumption).
  - Legacy compatibility routes mounted in ShreckRPG (`/games`, `/notes`, `/notifications`, `/page-visits`) while canonical APIs remain `/v1/*`.
  - Shrecknet extraction wave started with first-class domain models + legacy-compatible routes for:
    - `/v1/libraries` and `/libraries` (items + bookmarks + embedding status/trigger stubs)
    - `/v1/ontology-instances` and `/ontology-instances` (CRUD/search/summaries/favorites/events)
    - `/v1/jobs/*` and `/jobs/*` family routes (`architect`, `elder`, `elder/chats`, `librarian`, `novelist`)
  - Phase 3 importer/verifier alignment updated so `library_items` migrate into Shrecknet `library_items` (not generic media).
  - Shrecknet runtime foundation shifted to async-ready request flow:
    - async lifespan init (`init_db_async`)
    - async dependency/session entrypoint (`get_db_session`)
    - async-auth/core routers (`auth`, `users`, `worlds`, `contracts`, `agents`, `jobs`, `media`, `ontologies`)
    - SQLite-compatible async session wrapper to avoid `aiosqlite` runtime deadlocks while preserving async service signatures.
  - Heavy Shrecknet-owned routers converted to async session flow:
    - `/v1/libraries` + `/libraries`
    - `/v1/ontology-instances` + `/ontology-instances`
  - Agent-stack extraction wave staged into `shrecknet/app` (not yet fully wired in app startup):
    - `graph/neo4j.py`
    - `integrations/llm/*`
    - `graphrag/*`
    - `jobs/*`
    - `tasks/*`
    - agent job routers (`elder`, `elder_chats`, `architect`, `librarian`, `novelist`, `graphrag`)

## Local Validation

Validated in this workspace:

- `python -m compileall -q shrecknet shreckrpg`
- `cd shrecknet && PYTHONPATH=. ../.venv/bin/python -m pytest -q tests/test_jwks_auth_contract.py tests/test_phase2_persistence.py`
- `cd shreckrpg && PYTHONPATH=. ../.venv/bin/python -m pytest -q tests/test_event_idempotency.py`

## How to Start Splitting for Real

1. Export current monolith data into two ownership buckets.
2. Stand up both APIs with `docker-compose.two-repo.yml`.
3. Move frontend/API consumers:
   - world/agent/ontology/identity to Shrecknet
   - RPG flows to ShreckRPG
4. Persistence is now SQL-backed; next step is replacing seed/demo data with migration-loaded production data.
5. Add message bus/webhooks for outbound Shrecknet events to ShreckRPG.
6. Cut traffic over and retire monolith endpoints.

## Immediate Next Engineering Tasks

- Implement Celery workers in both repos and route jobs by domain ownership.
- Add OpenAPI contract tests between Shrecknet and ShreckRPG.
- Add migration replay/rollback validation scripts for production cutover.
