# CharacterAgent Decision Making

`POST /character-agents/{character_agent_id}/decision-making` starts a durable background
decision-making run and returns `202 Accepted`. Callers poll the returned `status_url` until
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
  "status_url": "/character-agents/agent-1/decision-making-jobs/481"
}
```

The submission verifies authentication, visibility, active status, AI-agent
availability, shreckLLM configuration, and the request contract before
enqueueing. The worker reloads the current identity immediately before
generation.

## Decision-making pipeline

Identity mode makes one substantive deliberation call. The worker loads the
persisted `identity_description` and only
`(:CharacterAgent)-[:HAS_PERSPECTIVE]->(:ScenePerspective)` records owned by
the queried agent. It deterministically ranks the character's own subjective
memories and supplies at most five relevant memories to the model. Perspective
memory content is a single subjective text field plus linked emotions, beliefs,
and impacts; the canonical scene itself is not sent.

A memory contains the perspective text, emotions, and historical beliefs. Legacy
impacts may remain visible for old records. Beliefs have no mutable lifecycle
status; their originating scene chronology distinguishes earlier from later
beliefs. Canonical `Scene` text, other characters' perspectives, and arbitrary
graph facts are never retrieved for this path. A memory is subjective rather
than objective truth; contradictory beliefs from different scenes can coexist.

Each perspective has a derived searchable memory document and embedding. It is
created or refreshed when the perspective aggregate changes. Failed or pending
embeddings fall back to owner-scoped lexical ranking; they never widen graph
scope. These vectors are separate from the ordinary `SemanticDocument` scene
corpus, so non-character scene search is unchanged.

The deliberation payload contains `identity_description` (`identity_summary`,
`psychological_summary`, and qualitative `personality_traits`), caller
query/context/instruction, response format, and selected memories. This persisted
description supplies established motives and individual meaning. Identity mode
always includes compact `personality_context` with effective directional
values/statuses and separate evidence-checked Steadiness. The existing request
flag `use_character_identity` is the sole switch: `true` includes the portrait,
numerical context, and memories; `false` uses generic mode without any of them. The raw `trait_profile`, authored `background_story`,
full goals/aspects, overrides, and evidence ledger are never model inputs.
Memory ranking uses only the query and
serialized caller context, including any people, options, and stakes supplied
there. Identity and `system_instruction` do not add search terms. Ranking remains
deterministic and limited to the queried character's own perspectives. Up to five
matching memories are sent; low-relevance memories remain excluded. The prompt
asks for the choice this character would make, including personal fears and
contradictions, rather than a generic optimal choice. It contains no opaque IDs,
evidence IDs, raw observation ledger, model-selected selectors, or framing
summaries.

### Personality and situational deliberation

`DECISION_MAKING_PROMPT` (`character-decision-v3-required-personality`)
asks the model to appraise the psychological demands and constraints, consider
only relevant tendencies and supported motives, then select a plausible action
inside the same call. These conceptual operations add no job stages or framing
call. Personality describes behavioral tendencies, not deterministic rules.
Even extreme traits permit contrary choices. Strong constraints may make
characters converge; factual questions need not display trait differences.
Supported personal motivations may outweigh generic safety or optimality.
The output remains caller-formatted `content` and a concise public
`decision_basis`, never private deliberation.

In identity mode, the service supplies `personality_context.version` as
`decision-personality-v1`. Its `dispositions` map contains exactly `integrity`,
`caution`, `presence`, `forbearance`, `diligence`, `curiosity`, `sharing`, and
`restlessness`. Each has `value` (integer 1–9 or null) and `status`
(`unknown`, `provisional`, `supported`, or `manual`). Unknown requires null;
5 means centered/mixed, not missing. Scores express ordinal direction and relative
strength, not action probabilities, percentiles, moral worth, or confidence.
The prompt's meanings and both poles come from the authoritative trait registry.
Effective values control dispositional direction, including manual edits that
conflict with an older portrait. The portrait supplies supported psychology;
the model must not invent why a score changed or discard historical memories.
Unknown numerical estimates leave supported narrative tendencies usable.

Separate `steadiness` carries `value`, `status`, `observation_count`,
`availability`, `reason`, and `inferred_value`, using the same evidence verification
as identity synthesis. The backend merges only this agent's revision ledger;
it sends no raw observations or provenance IDs. `available` requires the ledger
to reproduce the recorded inferred estimate. Missing, conflicting, or malformed
evidence produces `unavailable` and null `inferred_value`. Counts measure
contributing perspectives, not confidence. High Steadiness describes narrower
and low Steadiness broader variation in comparable circumstances; it is neither
per-trait variance nor emotional calmness. Unknown values support no consistency
claim. Provisional estimates are tentative. Manual values may supply tentative
authored variability without proving observed consistency. Steadiness never
changes caller temperature or randomly overrides an action.

Missing profiles become all-unknown context. Obsolete/invalid profiles retain
portrait-only behavior with unknown numerical values and a backend warning;
decisions do not regenerate profiles or portraits. If no contributing consistency
perspectives exist, loading context needs no extra evidence query. Otherwise it
adds one agent-scoped revision query, with cost proportional to revision history.
Database/service failures retain normal job failure behavior; optional evidence
validation failures alone do not prevent a decision.

### Configuration and rollout

`use_character_identity` defaults to `true` and is the only personality switch.
There is no separate server configuration toggle for numerical context. Generic
mode always excludes identity, numerical context, and character memories.
Public decision requests and results retain their existing fields.

Reasoning already uses `model_character_agent_decision_making.reasoning`; no default
is changed. Choose a supporting provider/model and compare reasoning separately
from prompt or numerical context. The configured provider owns its reasoning
behavior; no new framing model is invoked. Decisions retain existing asynchronous
polling without a whole-stage deadline. Measure added tokens, latency, and
revision-query cost before production promotion. See
[controlled evaluation](../Dispositional%20Traits.md#behavioral-identity-evaluation).

Deploy compatible API/workers together. No character-data migration, automatic portrait
resynthesis, historical re-extraction, or memory re-embedding is required. Use `use_character_identity=false` for generic decisions; restoring the prior
portrait-only identity implementation requires reverting the relevant code. Neither step changes completed
results or already persisted portraits. Preserve portraits before any deliberate
embodiment pilot replacement.

If `identity_description` is absent, identity mode fails the background job with
`error.code=identity_unavailable`; it does not substitute another personality
source. Generic mode remains available for an active, visible agent. Existing
agents need a persisted identity description before identity-mode decisions; no
graph migration or memory re-embedding is required.

The main call requests native strict JSON for the response envelope. Its output
is parsed and validated locally against the caller contract. Malformed output
receives at most one JSON-only repair through `model_agents_repair_json`; a
failed repair fails the job. This is the only exceptional second model call.
String fields named `rationale` retain the server-owned 2,000-character cap.

Generic mode also uses one call, never receives CharacterAgent identity, and
uses the same local validation and bounded repair policy. shreckLLM owns
provider retries.

## Polling

`GET /character-agents/{character_agent_id}/decision-making-jobs/{job_id}` requires the
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
    "decision_basis": "Past losses make the character cautious around a forced entry."
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
The former `/query` and `/query-jobs/{job_id}` paths remain as compatibility
aliases; new clients should use `/decision-making` and
`/decision-making-jobs/{job_id}`.
