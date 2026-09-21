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

1. **Framing** receives the query/context, character name, current trait profile,
   registry metadata, active aspects as `{id,name}`, and active goals as
   `{id,name,description}`. It selects directional traits by psychological
   affordance and returns `relevant_traits` entries with `trait`, `situation_type`,
   and `relevance`. The backend rejects invalid trait/situation pairs. Aspect/goal
   selectors retain exact-ID or unambiguous exact-name resolution.
2. **Deliberation** receives the original query, validated context summary,
   caller instruction, selected trait estimates with constructs/poles/boundaries
   and relevance, selected aspect/goal names, conflicts, unknowns and output format.
   STEADINESS is a separate consistency modifier only when directional traits apply.
   It is never selected as an ordinary predictor and never sets temperature.

Unknown traits remain unknown; point 5 means an evidenced midpoint. Trait values
bias choices probabilistically. Context, aspects and goals can outweigh those
biases. Framing preserves knowledge, capabilities, available options and compulsion
so the selected disposition matches a meaningful decision affordance.

Stage 2 receives no original raw context, background story, or aspect/goal IDs or
descriptions. Structured output is parsed/validated locally; one final JSON repair
is allowed through the configured repair target. Stage 1 is not repaired.
String fields named `rationale` retain the server-owned 2,000-character cap and
are deterministically truncated before response-schema validation.

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
