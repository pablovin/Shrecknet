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
- `limits.turn_query_max_length`: current frontend validation limit.
- `limits.turn_job_result_ttl_seconds`: result retention window.
- `media.base_url`: static media mount for uploaded companion assets.
- `media.companion_avatar_pattern`: stable avatar URL pattern, `/media/{username}/companion.png`.

Frontend-owned base URL example:

- `VITE_SHRECKCOMPANION_BASE_URL=http://localhost:8120`

Frontend polling type contract:

- `frontend_types.ts`: TypeScript contract for `queued`, `running`, `done`, and `failed` turn-result payloads.
- `done.payload.final.linked_text`: HTML-renderable final answer with inline node anchors.

## Turn Job Phases

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

### 1. Planning

Purpose:

- Herald is deciding which tools are needed, in which order, and whether later steps depend on earlier grounded evidence.

Payload fields:

- `status`: always `running`
- `phase`: `planning`
- `phase_label`: `Planning tool usage`
- `progress`: `{ "current": 1, "total": 4 }`
- plus the base turn context: `query`, `session_id`, `ontology_id`, `companion_id`, `allocated_tools`

Example:

```json
{
  "job_id": 8,
  "status": "running",
  "payload": {
    "status": "running",
    "query": "Who is Ernst?",
    "session_id": "9ff6774b-e06d-417a-9b71-cab0a33e028d",
    "ontology_id": 99,
    "companion_id": "companion-123",
    "allocated_tools": {
      "elder": [{ "id": "elder-1", "name": "Elder", "job": "elder", "ontology_ids": [99] }],
      "librarian": [{ "id": "librarian-1", "name": "Librarian", "job": "librarian", "ontology_ids": [99] }]
    },
    "phase": "planning",
    "phase_label": "Planning tool usage",
    "progress": { "current": 1, "total": 4 }
  }
}
```

### 2. Selecting Tools

Purpose:

- Routing finished and the backend has chosen which allocated tools will be called.

Payload fields:

- everything from `planning`
- `routing`: backward-compatible summary of whether `elder` and/or `librarian` are used
- `plan`: structured execution plan
- `selected_tools`: `{ elder: string[], librarian: string[] }`
- `phase`: `selecting_tools`
- `phase_label`: `Selecting tools`
- `progress`: `{ "current": 2, "total": 4 }`

Example:

```json
{
  "job_id": 8,
  "status": "running",
  "payload": {
    "status": "running",
    "query": "Who is Ernst?",
    "session_id": "9ff6774b-e06d-417a-9b71-cab0a33e028d",
    "ontology_id": 99,
    "companion_id": "companion-123",
    "allocated_tools": {
      "elder": [{ "id": "elder-1", "name": "Elder", "job": "elder", "ontology_ids": [99] }],
      "librarian": [{ "id": "librarian-1", "name": "Librarian", "job": "librarian", "ontology_ids": [99] }]
    },
    "phase": "selecting_tools",
    "phase_label": "Selecting tools",
    "progress": { "current": 2, "total": 4 },
    "routing": {
      "use_elder": true,
      "use_librarian": false,
      "reason": "Requires knowledge of a specific character and their role in the story."
    },
    "selected_tools": {
      "elder": ["elder-1"],
      "librarian": []
    }
  }
}
```

### 3. Executing Steps

Purpose:

- The backend is actively executing the planned tool steps, including sequential dependent steps.

Payload fields:

- everything from `selecting_tools`
- `step_progress`: `{ total, completed, running, current }`
- `current_step`: the currently executing step metadata
- `execution`: completed step records plus any stop reason
- `phase`: `executing_steps`
- `phase_label`: `Executing tool plan`
- `progress`: `{ "current": 3, "total": 4 }`

Example:

```json
{
  "job_id": 8,
  "status": "running",
  "payload": {
    "status": "running",
    "query": "Who is Ernst?",
    "session_id": "9ff6774b-e06d-417a-9b71-cab0a33e028d",
    "ontology_id": 99,
    "companion_id": "companion-123",
    "allocated_tools": {
      "elder": [{ "id": "elder-1", "name": "Elder", "job": "elder", "ontology_ids": [99] }],
      "librarian": [{ "id": "librarian-1", "name": "Librarian", "job": "librarian", "ontology_ids": [99] }]
    },
    "phase": "executing_steps",
    "phase_label": "Executing tool plan",
    "progress": { "current": 3, "total": 4 },
    "routing": {
      "use_elder": true,
      "use_librarian": true,
      "reason": "Needs both story context and rules context."
    },
    "selected_tools": {
      "elder": ["elder-1"],
      "librarian": ["librarian-1"]
    },
    "step_progress": {
      "total": 2,
      "completed": 0,
      "running": 1,
      "current": 1
    },
    "current_step": {
      "step_id": "step-1",
      "tool_job": "elder",
      "goal": "Gather grounded canon context."
    }
  }
}
```

### 4. Synthesizing

Purpose:

- Tool calls finished and Herald is generating the final answer text.

Payload fields:

- everything from `executing_steps`
- `agent_responses`: normalized tool outputs already collected from executed steps
- `step_progress`: usually `{ total: N, completed: N, running: 0 }`
- `phase`: `synthesizing`
- `phase_label`: `Synthesizing answer`
- `progress`: `{ "current": 4, "total": 4 }`

Example:

```json
{
  "job_id": 8,
  "status": "running",
  "payload": {
    "status": "running",
    "query": "Who is Ernst?",
    "session_id": "9ff6774b-e06d-417a-9b71-cab0a33e028d",
    "ontology_id": 99,
    "companion_id": "companion-123",
    "allocated_tools": {
      "elder": [{ "id": "elder-1", "name": "Elder", "job": "elder", "ontology_ids": [99] }],
      "librarian": [{ "id": "librarian-1", "name": "Librarian", "job": "librarian", "ontology_ids": [99] }]
    },
    "phase": "synthesizing",
    "phase_label": "Synthesizing answer",
    "progress": { "current": 4, "total": 4 },
    "routing": {
      "use_elder": true,
      "use_librarian": false,
      "reason": "Requires knowledge of a specific character and their role in the story."
    },
    "selected_tools": {
      "elder": ["elder-1"],
      "librarian": []
    },
    "tool_progress": {
      "total": 1,
      "completed": 1,
      "running": 0
    },
    "agent_responses": [
      {
        "ok": true,
        "agent_id": "elder-1",
        "agent_name": "Elder",
        "agent_job": "elder",
        "answer": "Based on the available records, Ernst is part of a Berlin circle...",
        "sources": []
      }
    ]
  }
}
```

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
- `POST /users/me/companion/avatar`
- `DELETE /users/me/companion`
- `POST /users/me/companion/orchestrator/bootstrap`
- `POST /users/me/companion/orchestrator/chats/{session_id}/turns`
- `GET /users/me/companion/orchestrator/turns/{job_id}`
- `GET /users/me/companion/orchestrator/chats/{session_id}/file`

User identity is resolved from `X-Shreck-User-Id` in v1, with fallback to `default_user_id` for local development. Calls that execute Shrecknet jobs should also forward the user's `Authorization: Bearer ...` token so Shrecknet can authorize `/jobs/elder/...` and `/jobs/librarian/...`. Avatar uploads also accept `X-Shreck-Username`; uploaded files are normalized and stored as `media/{username}/companion.png`, replacing the previous file for that user.
