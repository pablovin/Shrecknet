# Novelist Agent Endpoints

All endpoints require authentication and retain the `/jobs/novelist` prefix.

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
