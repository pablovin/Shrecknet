# Novelist Agent Endpoints

All endpoints require authentication and retain the `/jobs/novelist` prefix.

## Remembered preferences

The frontend retrieves an agent's most recently used Novelist preferences from
the existing agent endpoints:

- `GET /agents/` (optionally use `?job=novelist` to list Novelist agents)
- `GET /agents/{agent_id}` (retrieve the selected agent)

Both return `AgentRead`, which includes nullable `novelist_last_language` and
`novelist_last_instructions`. New or previously unused agents return `null` for
both fields. These values are updated only when a run is accepted.

Example response fields:

```json
{
  "id": "agent-uuid",
  "job": "novelist",
  "novelist_last_language": "fr",
  "novelist_last_instructions": "Keep names stable"
}
```

The text endpoint and upload endpoint accept `language` and `instructions`. When
either field is omitted or `null`, the saved value is reused. An explicit empty
string clears that preference and runs without it. The resolved values are stored
in the run request and returned on subsequent agent reads.

- `POST /jobs/novelist/{agent_id}/runs` accepts `unstructured_text`, optional
  `language`, `instructions`, `previous_session_id`, `previous_novelist_run_id`,
  `source_type`, and `source_label`.
- `POST /jobs/novelist/{agent_id}/runs/upload` accepts `.txt`, `.md`, `.json`,
  and `.pdf`, plus the same optional continuity and source fields.
- `GET /jobs/novelist/runs/{run_id}` and
  `GET /jobs/novelist/{agent_id}/runs` return `NovelistRunRead`.
- `DELETE /jobs/novelist/{agent_id}/runs/{run_id}` deletes a run.

`draft_text` remains the display-ready final chapter. V3 additionally reports
`pipeline_version`, `block_count`, `fidelity_status`, and `correction_count`.
Frontend clients should render server-provided status text and must not depend on
fixed stage names or artifact internals. The old V2 critic and scene diagnostic
fields are nullable compatibility fields only.
