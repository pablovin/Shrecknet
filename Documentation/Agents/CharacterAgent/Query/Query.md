# CharacterAgent Query

`POST /character-agents/{character_agent_id}/query` starts a durable background
query and returns `202 Accepted`. Callers poll the returned `status_url` until
the job status is `done` or `failed`; there is no synchronous whole-query HTTP
deadline.

## Submission

The request retains `query`, `use_character_identity`, `system_instruction`,
`context`, `response_format`, and `generation.temperature`. Identity mode is
the default.

```json
{
  "query": "Choose how to respond to the locked laboratory.",
  "use_character_identity": true,
  "system_instruction": "Choose exactly one supplied option.",
  "context": {
    "options": [
      {"id": "force-door", "label": "Force the door"},
      {"id": "find-key", "label": "Look for a key"}
    ]
  },
  "response_format": {
    "type": "json",
    "schema": {
      "type": "object",
      "required": ["choice_id"],
      "properties": {
        "choice_id": {"enum": ["force-door", "find-key"]}
      }
    }
  }
}
```

```json
{
  "job_id": 481,
  "status": "queued",
  "stage": "queued",
  "progress": 0.0,
  "status_url": "/character-agents/agent-1/query-jobs/481"
}
```

The submission verifies authentication, visibility, active status, AI-agent
availability, shreckLLM configuration, and the request contract before
enqueueing. The worker reloads the current identity immediately before
generation.

## Identity pipeline

Identity mode normally performs two LLM calls.

1. **Framing** receives the query, caller context, character name, all eight
   behavioral-axis values, trait adherence, active aspects as `{id,name}`, and
   active goals as `{id,name,description}`. It returns a one-paragraph
   `context_summary`, relevant trait names, aspect IDs, goal IDs, conflicts,
   and unknowns. The backend keeps supplied active IDs and may resolve a
   selector returned as a name only when it exactly matches one active aspect
   or goal after case and whitespace normalization. Unknown or ambiguous
   selectors are discarded, allowing deliberation to continue without them.
   Model-returned selector text is never passed directly to deliberation.
2. **Deliberation** receives only the original query, context summary, system
   instruction, selected axes as name/value/scale explanation, selected aspect
   names, selected goal names, conflicts, unknowns, and response-format
   contract. It returns `content` and a one-paragraph `decision_basis`.

Stage 2 never receives the original context, background story, trait adherence,
identity IDs, aspect descriptions, or goal descriptions. Final output is
parsed and validated locally. If it is malformed or violates the caller schema,
one JSON-repair LLM call is allowed; repaired output must validate or the job
fails. The repair call receives the required top-level `content` and
`decision_basis` envelope schema, with `content` constrained by the caller's
response schema. Stage 1 is never repaired.

For JSON content, Shrecknet owns the length policy for every string field named
`rationale`: the effective schema allows up to 2,000 characters, regardless of
a lower caller-provided `maxLength`. Returned rationale text longer than 2,000
characters is deterministically truncated before schema validation. Exceeding
this cap does not trigger repair and does not fail the job; all other caller
schema constraints continue to validate normally.

Generic mode also uses two normal calls. Neutral framing receives only the
original query and caller context and must return empty identity-selector
arrays. Generic deliberation receives the validated context summary, conflicts,
unknowns, system instruction, and response-format contract. Neither call
receives or simulates CharacterAgent identity. It uses the same deterministic
validation and optional final repair.
Shrecknet does not resubmit timed-out stages; shreckLLM owns provider retries.

## Polling

`GET /character-agents/{character_agent_id}/query-jobs/{job_id}` requires the
initiating user or an administrator. A job is visible only under its owning
CharacterAgent.

Stages are `queued`, `loading_identity`, `framing`, `deliberating`,
`repairing`, `validating`, `completed`, and `failed`. Invalid model output may
receive one repair attempt through the global `model_agents_repair_json` target.
Polling a failed job still
returns HTTP `200`; failure is represented by `status=failed` and the typed
`error`.

Completed result:

```json
{
  "job_id": 481,
  "character_agent_id": "agent-1",
  "status": "done",
  "stage": "completed",
  "progress": 1.0,
  "result": {
    "type": "json",
    "content": {"choice_id": "find-key"},
    "decision_basis": "The selected traits and objectives favor an authorized route."
  },
  "error": null,
  "created_at": "2026-07-27T13:20:00Z",
  "updated_at": "2026-07-27T13:20:22Z",
  "completed_at": "2026-07-27T13:20:22Z"
}
```

Query jobs retain only safe stage metadata and terminal output in
`BackgroundJob.details`; full caller context and identity snapshots are not
stored there. V1 provides no cancellation endpoint or query-specific expiry.
