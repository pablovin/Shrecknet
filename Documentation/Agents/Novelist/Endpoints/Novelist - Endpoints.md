# Novelist Agent Endpoints

All endpoints require authentication and retain the `/jobs/novelist` prefix.

## Remembered preferences

The frontend retrieves the selected agent's nullable
`novelist_last_language` and `novelist_last_instructions` through `GET /agents/`
or `GET /agents/{agent_id}`. Omitted or `null` values reuse the saved preference.
An explicit empty string clears it. Preferences are updated when a run is accepted.

## Run lifecycle

- `POST /jobs/novelist/{agent_id}/runs` accepts `unstructured_text`, optional
  `language`, `instructions`, `previous_session_id`, `previous_novelist_run_id`,
  `source_type`, and `source_label`.
- `POST /jobs/novelist/{agent_id}/runs/upload` accepts `.txt`, `.md`, `.json`, and
  `.pdf`, plus the same optional continuity and source fields.
- `GET /jobs/novelist/runs/{run_id}` and
  `GET /jobs/novelist/{agent_id}/runs` return `NovelistRunRead`.
- `DELETE /jobs/novelist/{agent_id}/runs/{run_id}` deletes a run.

`previous_novelist_run_id` may identify a completed V4 run or a completed V3 run.
Unknown, incomplete, or unsupported historical runs contribute no previous-run
context and do not override current source material.

`draft_text` remains safe, display-ready chapter HTML. V4 returns
`pipeline_version: "v4"`, `block_count`, `fidelity_status: "not_run"`, and
`correction_count: 0`. The fidelity fields remain present only for response
compatibility; V4 does not execute an LLM fidelity stage. Old V2 scene and critic
diagnostics remain nullable compatibility fields.

Clients should render server-provided progress text and must not branch core UX on
fixed stage names or artifact internals.
