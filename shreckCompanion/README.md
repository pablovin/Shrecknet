# ShreckCompanion

Standalone Herald companion orchestration service.

This service owns personal companion state, chat sessions, turn jobs, SQLite persistence, and JSON chat/export mirrors. It delegates specialist knowledge retrieval to Shrecknet provider APIs and delegates text generation to shreckLLM.

## App Layout

- `app/api/`: HTTP routes and request dependencies.
- `app/core/`: settings and config seed loading.
- `app/jobs/`: Herald planning, orchestration, synthesis, and prompts.
- `app/integrations/`: shreckLLM and Shrecknet provider clients.
- `app/persistence/`: SQLite storage and JSON chat/frontend exports.
- `app/schemas.py`: API and persistence DTOs shared across layers.

## Frontend Config

The frontend can read `GET /config/frontend` from whichever ShreckCompanion base URL it is configured to call.

Public fields:

- `models.personal_companion_routing`: provider/name pair for Herald planning.
- `models.personal_companion_synthesis`: provider/name pair for Herald synthesis.
- `headers.authorization`: user bearer token forwarded to Shrecknet job endpoints.
- `headers.user_id`: user identity header expected by v1.
- `endpoints`: relative API paths for companion CRUD, bootstrap, queue turn, poll turn, and chat file.
- `endpoints.companion_rapport`: read-only user-companion rapport snapshot.
- `limits.turn_query_max_length`: current frontend validation limit.
- `limits.turn_job_result_ttl_seconds`: result retention window.
- `media.base_url`: static media mount for uploaded companion assets.
- `media.companion_avatar_pattern`: stable avatar URL pattern, `/media/{username}/companion.png`.

Frontend-owned base URL example:

- `VITE_SHRECKCOMPANION_BASE_URL=http://localhost:8120`

Frontend polling type contract:

- `frontend_types.ts`: TypeScript contract for `queued`, `running`, `done`, and `failed` turn-result payloads.
- `done.payload.final.linked_text`: HTML-renderable final answer with inline node anchors.

## Turn Lifecycle

The frontend polls `GET /users/me/companion/orchestrator/turns/{job_id}`.

Envelope shape:

```json
{
  "job_id": 8,
  "status": "running",
  "payload": {}
}
```

While the backend is working, `status` stays `running` and `payload.phase` changes through four values.
While the backend is working, `status` stays `running` and `payload.phase` moves through six values.

Deterministic pipeline order (logical lifecycle):

1. Load state
2. Lifecycle policy
3. Knowledge planning (Elder/Librarian only)
4. Knowledge execution
5. Synthesis
6. Reflection evaluator
7. Optional repair (max one pass)
8. Optional proactive nudge (max one short nudge)
9. Apply bounded state updates
10. Persist final payload

Observable running phases in turn payload:

1. `policy` (`Planning companion policy`) progress `{ "current": 1, "total": 6 }`
2. `planning` (`Planning tool usage`) progress `{ "current": 2, "total": 6 }`
3. `selecting_tools` (`Selecting tools`) progress `{ "current": 3, "total": 6 }`
4. `executing_steps` (`Executing tool plan`) progress `{ "current": 4, "total": 6 }`
5. `synthesizing` (`Synthesizing answer`) progress `{ "current": 5, "total": 6 }`
6. `reflection` (`Evaluating response quality`) progress `{ "current": 6, "total": 6 }`

Done payload lifecycle metadata:

- `companion_policy`
- `turn_reflection`
- `chat_state`
- `rapport_profile`
- `rapport_patch_applied`

### Efficiency And Parallelism Rules

The lifecycle remains deterministic, but internal work can run concurrently when data dependencies allow it.

Safe concurrency opportunities:

1. In `executing_steps`, independent knowledge steps can run in parallel only when there are no `depends_on` constraints.
2. Reflection/state update persistence can run after final text is produced, and can be optimized to avoid blocking frontend response in future async mode.
3. Non-LLM transforms (normalization, payload shaping, reference linking, bounded patch validation) should stay local and deterministic.

Concurrency guardrails:

1. Keep lifecycle stage ordering fixed.
2. Never run Librarian before required Elder grounding when a step depends on canon context.
3. Keep `max one repair` and `max one proactive nudge` hard-limited.
4. Apply rapport updates only through bounded, confidence-gated patches.

## Docker Compose

Run the companion as a separate stack after the root Shrecknet stack is healthy:

- `./run.sh`

The launcher reads `configs/shreckcompanion.json` and publishes the same host port as the JSON `port` value, so the config file is the only place you normally change it.

You can still run Compose directly or override the host port when debugging:

- `SHRECKCOMPANION_HOST_PORT=8121 docker compose up --build`

When the service is healthy, the `shreckcompanion_ready` container prints the active host URL and useful endpoints.

## Local API

- `GET /health`
- `GET /ready`
- `GET /status`
- `GET /config`
- `GET /config/frontend`
- `POST /users/me/companion`
- `GET /users/me/companion`
- `PATCH /users/me/companion`
- `GET /users/me/companion/rapport`
- `POST /users/me/companion/avatar`
- `DELETE /users/me/companion`
- `POST /users/me/companion/orchestrator/bootstrap`
- `POST /users/me/companion/orchestrator/chats/{session_id}/turns`
- `GET /users/me/companion/orchestrator/turns/{job_id}`
- `GET /users/me/companion/orchestrator/chats/{session_id}/file`

User identity is resolved from `X-Shreck-User-Id` in v1, with fallback to `default_user_id` for local development. Calls that execute Shrecknet jobs should also forward the user's `Authorization: Bearer ...` token so Shrecknet can authorize `/jobs/elder/...` and `/jobs/librarian/...`. Avatar uploads also accept `X-Shreck-Username`; uploaded files are normalized and stored as `media/{username}/companion.png`, replacing the previous file for that user.

## Companion Core Personality And Rapport

Companion profile is canonical companion identity and is editable through companion CRUD endpoints. It includes:

1. `core_traits`
2. `archetype`
3. `voice`
4. `boundaries`
5. `default_style` (`verbosity`, `humor`, `directness`, `initiative`)

Companion rapport is canonical user-companion adaptive state and is exposed as read-only frontend data at `GET /users/me/companion/rapport`.

Ownership rules:

1. Core personality is updated only through frontend/manual companion updates.
2. Rapport is updated only by lifecycle backend logic using bounded patches.
