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

Identity mode makes one substantive deliberation call. The worker loads the
active identity, all active aspects/goals, and only active
`(:CharacterAgent)-[:HAS_PERSPECTIVE]->(:ScenePerspective)` records owned by
the queried agent. It deterministically ranks the character's own subjective
memories and supplies at most five relevant memories to the model.

A memory contains the perspective's remembered summary, interpretation,
reflection, emotions, beliefs (including their current status), and lasting
impacts. Canonical `Scene` text, other characters' perspectives, and arbitrary
graph facts are never retrieved for this path. A memory is subjective rather
than objective truth; doubted, disproven, and superseded beliefs remain
historical beliefs.

Each perspective has a derived searchable memory document and embedding. It is
created or refreshed when the perspective aggregate changes. Failed or pending
embeddings fall back to owner-scoped lexical ranking; they never widen graph
scope. These vectors are separate from the ordinary `SemanticDocument` scene
corpus, so non-character scene search is unchanged.

The deliberation payload includes the persistent `identity_description` as the
original psychological foundation, alongside compact traits (point, status, poles, and a short
backend-derived evidence summary), steadiness, complete
active aspects/goals with descriptions, caller query/context/instruction, and
the selected memories. Demonstrated development in current traits, goals,
aspects, and memories takes precedence when it conflicts with the original
description. It contains no opaque IDs, evidence IDs, raw observation
ledger, model-selected selectors, or framing summaries. Unknown traits remain
unknown; point 5 can reflect mixed evidence. Traits bias behaviour while goals,
aspects, memories, and current context may outweigh them.

The main call requests native strict JSON for the response envelope. Its output
is parsed and validated locally against the caller contract. Malformed output
receives at most one JSON-only repair through `model_agents_repair_json`; a
failed repair fails the job. This is the only exceptional second model call.
String fields named `rationale` retain the server-owned 2,000-character cap.

Generic mode also uses one call, never receives CharacterAgent identity, and
uses the same local validation and bounded repair policy. shreckLLM owns
provider retries.

## Polling

`GET /character-agents/{character_agent_id}/query-jobs/{job_id}` requires the
initiating user or an administrator. A job is visible only under its owning
CharacterAgent.

Stages are `queued`, `loading_identity`, `retrieving_memories`, `deliberating`,
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
