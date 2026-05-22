# Architect Generate Lifecycle

This page covers generation after proposals are reviewed.

## SDK methods and endpoint coverage

| SDK method | Endpoint |
|---|---|
| `sdk.architect.get_run(run_id)` | `GET /jobs/architect/runs/{run_id}` |
| `sdk.architect.generate(run_id, payload)` | `POST /jobs/architect/runs/{run_id}/generate` |
| `sdk.architect.wait_for_generation(run_id, ...)` | SDK helper: wait for `generation_job_id`, then poll `/jobs/{id}` |

## Operational sequence

1. Load an existing run.
2. Build `ArchitectGenerationRequest` using reviewed pipeline output.
3. Trigger generation.
4. Wait for `generation_job_id` terminal state.
5. Re-fetch run and inspect final status and reconciliation details.

## Example

```bash
python python_sdk/examples/08_architect/02_generate_lifecycle.py
```

## Notes

- `generation_job_id` may be attached after the initial generate response.
- Generation failures are surfaced by generic job errors (`JobFailedError`, `JobTimeoutError`).
