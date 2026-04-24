# Novelist Agent Endpoints (Current API)

Implemented router:

- `shrecknet/app/api/routers/novelist.py`

Base prefix:

- `/jobs/novelist`

Authentication:

- All endpoints require authenticated user context.

## 1. Start Run

Endpoint:

- `POST /jobs/novelist/{agent_id}/runs`

Purpose:

- Create a Novelist run and start async draft generation.

Request body (`NovelistRunCreate`):

```json
{
  "unstructured_text": "Raw narrative/session text",
  "language": "en",
  "instructions": "Keep names and continuity consistent",
  "previous_session_id": "entity-123"
}
```

Response:

- `202 Accepted`
- `NovelistRunRead`

## 2. Start Run from Upload

Endpoint:

- `POST /jobs/novelist/{agent_id}/runs/upload`

Form fields:

- `file` (required): `.txt` or `.pdf`
- `language` (optional)
- `instructions` (optional)
- `previous_session_id` (optional)

Response:

- `202 Accepted`
- `NovelistRunRead`

## 3. Get Run

Endpoint:

- `GET /jobs/novelist/runs/{run_id}`

Response:

- `200 OK`
- `NovelistRunRead`

## 4. List Runs by Agent

Endpoint:

- `GET /jobs/novelist/{agent_id}/runs?limit=20&offset=0`

Query params:

- `limit`: integer, min 1, max 100, default 20
- `offset`: integer, min 0, default 0

Response:

- `200 OK`
- `list[NovelistRunRead]`

## 5. Delete Run

Endpoint:

- `DELETE /jobs/novelist/{agent_id}/runs/{run_id}`

Response:

```json
{ "deleted": 1 }
```

## Response Fields for Frontend

API run reads return `NovelistRunRead`, which includes:

- lifecycle: `status`, `stage`, `error_message`
- request context: `request_payload`, `previous_session_id`, `previous_session_summary`
- persisted outputs: `draft_text`, `critic_notes`
- diagnostics: `artifacts`, `timing_summary`, `stage_timings`, `scene_progress`
- optional derived views from artifacts: `step_outputs`, `scene_results`

## Current Novelist Output Contracts

### Internal orchestrator output (used by task layer)

```json
{
  "scene_packages": [ ... ],
  "critic_remarks": { ... },
  "final_text_html": "..."
}
```

### Persisted run output (exposed via API)

- `draft_text` stores `final_text_html`.
- `critic_notes` stores serialized `critic_remarks`.
- rich stage/debug structures remain under `artifacts`.

## Debug Output Files (Run Artifacts)

For the local debug output directory, step response files are:

- `step_1_response.json`: scene scaffolding payload
- `step_2_response.json`: `{ "scene_packages": [...] }`
- `step_3_response.json`: `{ "scene_packages": [...] }`
- `step_4_response.json`: `{ "scene_packages": [...] }`
- `step_5_response.json`: `{ "scene_packages": [...] }`

Steps 2-5 are intentionally scene-packages-only for easier debugging.
