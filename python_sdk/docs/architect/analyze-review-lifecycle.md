# Architect Analyze and Review Lifecycle

This page covers the analysis and proposal curation phase for Architect.

## SDK methods and endpoint coverage

| SDK method | Endpoint |
|---|---|
| `sdk.architect.preflight(agent_id, strict=False)` | SDK composite check (LLM/provider + agent active/job=architect) |
| `sdk.architect.analyze(agent_id, request)` | `POST /jobs/architect/{agent_id}/analyze` |
| `sdk.architect.get_run(run_id)` | `GET /jobs/architect/runs/{run_id}` |
| `sdk.architect.list_runs(agent_id, ...)` | `GET /jobs/architect/{agent_id}/runs` |
| `sdk.architect.delete_run(agent_id, run_id)` | `DELETE /jobs/architect/{agent_id}/runs/{run_id}` |
| `sdk.architect.delete_runs(agent_id)` | `DELETE /jobs/architect/{agent_id}/runs` |
| `sdk.architect.update_proposal_statuses(run_id, proposal_ids, status)` | `PATCH /jobs/architect/runs/{run_id}/proposals/status` |
| `sdk.architect.create_proposal(run_id, payload)` | `POST /jobs/architect/runs/{run_id}/proposals` |
| `sdk.architect.patch_proposal(run_id, proposal_id, payload)` | `PATCH /jobs/architect/runs/{run_id}/proposals/{proposal_id}` |
| `sdk.architect.put_proposal(run_id, proposal_id, payload)` | `PUT /jobs/architect/runs/{run_id}/proposals/{proposal_id}` |
| `sdk.architect.wait_for_analysis(run_id, ...)` | SDK helper: wait for `background_job_id`, then poll `/jobs/{id}` |

## Operational sequence

1. Run preflight for LLM/provider and agent readiness.
2. Trigger analyze for the target architect agent.
3. Wait until `background_job_id` is attached and terminal.
4. Inspect proposals from `get_run`.
5. Curate proposals using status update and patch/put calls.

## Example

```bash
python python_sdk/examples/08_architect/01_analyze_and_review_lifecycle.py
```

## Notes

- `background_job_id` may appear asynchronously on the run record.
- Prefer `wait_for_analysis(...)` instead of hardcoding polling loops.
