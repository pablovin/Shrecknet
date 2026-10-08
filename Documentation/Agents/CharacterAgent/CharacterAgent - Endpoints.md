# CharacterAgent endpoints

## Authorization and visibility

CharacterAgent creation and maintenance remain administrator-only. This includes
creating, updating, deleting, selecting embodiment candidates, assigning aspects,
pursuing goals, and the global `/character-aspects` and `/character-goals` CRUD
routes.

Authenticated users may use these read/query routes:

- `GET /character-agents`
- `GET /character-agents/{character_agent_id}`
- `GET /character-agents/{character_agent_id}/aspects`
- `GET /character-agents/{character_agent_id}/goals`
- Administrator `PATCH /character-agents/{character_agent_id}/goals/{goal_id}`
- `GET /character-agents/{character_agent_id}/perspectives`
- `GET /character-agents/{character_agent_id}/perspectives/{perspective_id}`
- Nested perspective emotion, belief, and impact `GET` routes
- `POST /character-agents/{character_agent_id}/query`

For a non-administrator, these routes expose only agents whose `visibility` is
`public`. Direct access to a private or nonexistent agent returns `404`.
Administrators see public and private agents. `visibility` accepts `private` or
`public`, defaults to `private`, and is changed through the existing
administrator-only `PATCH /character-agents/{character_agent_id}` route. Graph
records without this property are treated as private.

Legacy agent nodes with retired personality fields are included in administrator
list and detail reads. Those fields are omitted from the current response shape;
an absent `trait_profile` is returned as the current unknown profile. The normal
administrator-only `DELETE /character-agents/{character_agent_id}` route can be
used to remove them.

## Authenticated-user examples

The examples use an OAuth bearer access token:

```bash
ACCESS_TOKEN="<access-token>"
```

List public agents, optionally filtered and paginated:

```bash
curl -sS \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://shrecknet.example/character-agents?status=active&skip=0&limit=20"
```

Example response:

```json
[
  {
    "ontology_id": 12,
    "entity_instance_id": "entity-mara",
    "name": "Mara",
    "background_story": "A guarded ruler responsible for a frontier village.",
    "identity_description": {
      "identity_summary": "A frontier ruler shaped by responsibility for her village.",
      "psychological_summary": "She fears failing those who depend on her and struggles to balance safety with trust.",
      "personality_traits": [{"trait": "caution", "description": "She seeks safeguards before exposing others to uncertain danger."}]
    },
    "image_url": null,
    "status": "active",
    "visibility": "public",
    "trait_profile": {
      "version": "dispositions-v3-points-perspectives",
      "dispositional_traits": {
        "integrity": {"point": null, "status": "unknown", "observation_count": 0, "observation_ids": []},
        "caution": {"point": null, "status": "unknown", "observation_count": 0, "observation_ids": []},
        "presence": {"point": null, "status": "unknown", "observation_count": 0, "observation_ids": []},
        "forbearance": {"point": null, "status": "unknown", "observation_count": 0, "observation_ids": []},
        "diligence": {"point": null, "status": "unknown", "observation_count": 0, "observation_ids": []},
        "curiosity": {"point": null, "status": "unknown", "observation_count": 0, "observation_ids": []},
        "sharing": {"point": null, "status": "unknown", "observation_count": 0, "observation_ids": []},
        "restlessness": {"point": null, "status": "unknown", "observation_count": 0, "observation_ids": []}
      },
      "steadiness": {"point": null, "status": "unknown", "observation_count": 0, "observation_ids": []},
      "overrides": {}, "inferred_traits": {}
    },
    "id": "agent-8c01",
    "embodied_entity_instance_id": "entity-mara",
    "created_by_user_id": 1,
    "created_at": "2026-07-25T09:00:00Z",
    "updated_at": "2026-07-25T09:30:00Z"
  }
]
```

Retrieve one public agent:

```bash
curl -sS \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://shrecknet.example/character-agents/agent-8c01"
```

The response is one object with the same fields as a list item.

Retrieve the agent's assigned aspects:

```bash
curl -sS \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://shrecknet.example/character-agents/agent-8c01/aspects"
```

Example response:

```json
[
  {
    "aspect": {
      "ontology_id": 12,
      "name": "Village negotiator",
      "normalized_name": "village negotiator",
      "category": "role",
      "description": "Represents the village in difficult negotiations.",
      "obtained_from_scene_id": null,
      "id": "aspect-31",
      "created_at": "2026-07-25T09:05:00Z",
      "updated_at": "2026-07-25T09:05:00Z"
    },
    "status": "active",
    "in_focus": true,
    "justification": "Repeatedly negotiates on the village's behalf.",
    "evidence_ids": ["scene:scene-31"],
    "created_at": "2026-07-25T09:10:00Z",
    "updated_at": "2026-07-25T09:10:00Z"
  }
]
```

Retrieve the agent's pursued goals:

```bash
curl -sS \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://shrecknet.example/character-agents/agent-8c01/goals"
```

Example response:

```json
[
  {
    "goal": {
      "ontology_id": 12,
      "title": "Protect the villagers",
      "description": "Keep the settlement safe without abandoning its people.",
      "goal_type": "obligation",
      "obtained_from_scene_id": null,
      "id": "goal-17",
      "created_at": "2026-07-25T09:06:00Z",
      "updated_at": "2026-07-25T09:06:00Z"
    },
    "status": "active",
    "in_focus": true,
    "justification": "The character has accepted this obligation.",
    "evidence_ids": ["scene:scene-31"],
    "created_at": "2026-07-25T09:06:00Z",
    "updated_at": "2026-07-25T09:06:00Z"
  }
]
```

If `agent-8c01` is private, each direct non-administrator request above returns:

```json
{
  "detail": "CharacterAgent not found"
}
```

with HTTP status `404`.

## Scene perspectives

Perspective mutations require an administrator. Authenticated reads follow the
owning CharacterAgent's visibility rules.

- `GET|POST /character-agents/{character_agent_id}/perspectives`
- `GET|PATCH|DELETE /character-agents/{character_agent_id}/perspectives/{perspective_id}`
- `GET|POST /character-agents/{character_agent_id}/perspectives/{perspective_id}/emotions`
- `GET|PATCH|DELETE .../emotions/{emotion_id}`
- Equivalent nested CRUD under `beliefs`
- Legacy impact GET routes remain readable; impacts are not generated by embodiment

The perspective list accepts `skip` and `limit` and contains lightweight
perspective records. The detail response embeds ordered `emotions`, `beliefs`,
and legacy `impacts`. New embodiment returns no impacts. Beliefs contain only `statement` and `confidence`; the perspective payload contains `source_type` and `perspective`;
the retired summary, reflection, confidence, awareness, memory-strength,
importance, and lifecycle fields are no longer accepted or returned.
Legacy graph records remain readable: `perspective` is composed from the old
`interpretation` and `character_reflection` fields, with `summary` as a fallback
when those fields are absent. Application startup persists that conversion and
removes the old fields; obsolete memory embeddings are cleared and use lexical
retrieval until their perspective is refreshed.

Example perspective creation:

```json
{
  "scene_id": "scene-31",
  "source_type": "witnessed",
  "perspective": "The keep can no longer protect its people. I fear we were not ready."
}
```

Example emotion, belief, and impact payloads:

```json
{"arousal": 85, "valence": 2, "description": "Angry and frustrated."}
```

```json
{"statement": "Lancelot killed the guard.", "confidence": 60}
```

```json
{
  "impact_type": "goal_change",
  "direction": "advanced",
  "magnitude": 80,
  "description": "The confession strengthens the need for justice.",
  "target_id": "goal-17",
  "caused_by_milestone_id": "milestone-91"
}
```

A duplicate agent/scene pair returns `409`. Missing resources return `404`.
Invalid scope, scene eligibility, assigned impact targets, or causal milestones
return `400`; request-contract violations return `422`. Deleting a perspective,
agent, or projected canonical scene cascades perspective-owned children.
Embodiment-draft acceptance validates every generated timeline scene against the
same scope rule used during generation: the scene must relate directly to the
embodied entity or contain a milestone that relates to it. Stale denormalized `Scene.instance_id` metadata does not invalidate an otherwise
eligible graph relationship; a scene removed or detached after generation does. A scene that was removed or detached after generation returns `409` and
requires regeneration.

Embodiment-draft acceptance preserves historical aspects and goals. Lifecycle
status and `in_focus` are stored on character-owned `HAS_ASPECT` and `PURSUES`
relationships. Leaving focus does not change status; only ten active items per
category may be focused. Legacy `CharacterImpact` records remain available.

## Query

`POST /character-agents/{character_agent_id}/query` requires authentication and
enabled AI agents and returns `202` with a background job ID and `status_url`.
Poll `GET /character-agents/{character_agent_id}/query-jobs/{job_id}` until its
status is `done` or `failed`. Non-administrators may submit and read only their
own jobs for public agents; administrators may use public or private agents.
The agent must be active.

`use_character_identity` defaults to `true` and normally performs one
identity deliberation/rendering call. Before it, the worker deterministically
retrieves up to five relevant active `ScenePerspective` records owned by the
queried CharacterAgent; it never reads objective Scene content or another
character's memory. Invalid final JSON may receive one repair attempt through
`model_agents_repair_json`. Generic mode also uses one call and receives no
CharacterAgent identity.
shreckLLM owns provider retries; Shrecknet
polls the submitted shreckLLM job without a whole-stage deadline.

`generation` contains only `temperature`; removed `generation.mode` and
`generation.max_tokens` fields return `422`.

In structured JSON results, fields named `rationale` use a server-owned
2,000-character maximum. Longer generated values are truncated to 2,000
characters before validation rather than causing repair or job failure.

See [CharacterAgent Query](Query/Query.md) for request and response contracts.

## Timeline display references

`GET /character-agents/embodiment-drafts/{draft_id}` includes display-ready
references inside `timeline.source_projections`. Each new source projection has
`source_group`; each perspective has `scene` and `evidence`; each projected
impact has `target`; and each trait change has `evidence`. A reference always
contains stable `id`, `type`, human-readable `name`, optional `description`, and
optional `instance_name`. Clients may retain IDs for joins, but should render
these display fields rather than opaque identifiers. Existing drafts generated
before this contract may omit the new objects; regenerate the draft to obtain
complete display references.

## Personality and history

CRUD remains under `/character-agents`, `/character-aspects`, and `/character-goals`.
CharacterAgent reads include `trait_profile.dispositional_traits` and separate
`trait_profile.steadiness`. Every slot has `point` (integer 1–9 or null), status, `observation_count`,
and `observation_ids` containing `ScenePerspective.id` values. Unknown is null,
not point 5. See the complete
[trait specification and policy](Dispositional%20Traits.md).

Reads also return `identity_description`, an object with `identity_summary`,
`psychological_summary`, and `personality_traits` entries (`trait`, `description`).
It is server-generated during embodiment, is included in the reviewed draft
proposal, and is stored when that draft is accepted. Legacy agents without the
property return `null`; it is not part of caller-supplied create/update fields.

Authenticated metadata: `GET /character-agents/trait-definitions` returns the
registry and 1–9 point scale.

Administrator create/PATCH inputs use point edits rather than raw profiles:

```json
{
  "trait_edits": {
    "integrity": {"point": 7, "reason": "Authored character sheet."},
    "sharing": {"point": 3, "reason": "Scrupulous but ungenerous."}
  }
}
```

Each slot accepts a strict integer from 1 through 9. Omitted slots are retained. A nonblank
reason is required. `{"trait_edits":{"integrity":{"point":null,"reason":"Resume evidence."}}}`
clears that manual override and restores the current inferred value, possibly unknown.
Raw z values, caller-invented evidence, unknown trait names, and removed personality fields
are not accepted as edits (`422`). Graph mutation remains administrator-only.
Manual values stay effective while underlying evidence accumulates.
Reading an agent whose stored trait profile still uses the old format returns
`409` with a regeneration message; it is never silently converted to points.

`GET /character-agents/{agent_id}/revisions` returns immutable profile snapshots,
batch IDs and scene IDs. `GET /character-agents/{agent_id}/identity-changes` accepts
`change_type=trait|steadiness|subtitle|aspect|goal`. Changes include previous/new
estimate objects, justification, canonical evidence IDs, observation IDs, policy
version, and actor ID for manual changes.

`GET /character-agents/{agent_id}/trait-evidence` is administrator-only. Filters:
`trait` (directional key), `revision` (inclusive cutoff), `skip` (default 0), and
`limit` (default 100, maximum 500). It returns structured observations with
perspective IDs, polarity, context, justification, and revision provenance.
It includes observations even when they do not change the rounded point. Raw trait
observations are not included in public revision responses. Visibility rules for
existing profile and history reads continue to apply.

## Embodiment generation and reviewed creation

1. Administrator submits `POST /character-agents/embodiment-drafts` with
   `{"ontology_id":12,"entity_instance_id":"entity-mara"}`. AI agents must be
   enabled and configured. For an update proposal, include
   `target_character_agent_id`. Response `202` includes draft/job IDs and polling URLs.
   A repeated request for the same active draft returns its existing identifiers.
   To replace a terminal proposal after confirmation, set `replace_existing: true`.
   An active queued or generating draft cannot be replaced.
2. Poll `GET /jobs/{job_id}` and
   `GET /character-agents/embodiment-drafts/{draft_id}`. To rediscover the
   administrator's recent drafts after navigation or reload, call
   `GET /character-agents/embodiment-drafts?ontology_id=12&limit=20`. This
   admin-only list includes draft/job IDs, target agent, status, timestamps, and
   safe failure text; it is scoped to the authenticated administrator. States remain `queued`,
   `generating`, `ready`, `failed`, and `accepted`.
   When the selected LLM provider or model is unavailable, a failed draft's
   `error_message` names the provider, selected model, and safe availability
   reason, then instructs the administrator to configure an available model and
   retry. Job details additionally use `failure_category: "provider_unavailable"`.
3. Review `proposal.identity_description` alongside `proposal.trait_profile`,
   aspects/goals, source evidence and timeline. The description is generated or
   reused before scene processing, refreshed once from the final state, and
   committed to the CharacterAgent when the draft is accepted.
   The server-owned profile is the inference baseline; do not submit that entire
   read object as a write payload.
4. For a new agent, submit the normal creation aggregate with the draft ID and
   any manual point edits. For an update proposal, submit the reviewed profile fields,
   complete aspect/goal arrays, and draft ID through
   `PATCH /character-agents/{character_agent_id}`. Identical point values preserve
   inferred provenance; different point values produce a final manual revision after
   the generated timeline. Updating an existing agent appends its generated
   source revisions and commits the reviewed profile and assignments atomically.

```json
{
  "ontology_id": 12,
  "entity_instance_id": "entity-mara",
  "embodiment_draft_id": "draft-123",
  "name": "Mara of the Frontier",
  "background_story": "The administrator-reviewed history.",
  "visibility": "private",
  "trait_edits": {"integrity":{"point":7,"reason":"Reviewed authored characterization."}},
  "aspects": [],
  "goals": []
}
```

Creation materializes the graph aggregate atomically. Embedded aspect/goal
provenance must belong to the draft. Retrying an accepted draft returns its
existing agent. Manual creation without a draft starts unobserved slots as unknown.
Name/story/image derivation continues to use canonical entity information.

Update requests use the same fields as a reviewed create payload except that
`ontology_id` and `entity_instance_id` remain owned by the existing agent:

```json
{
  "embodiment_draft_id": "draft-123",
  "name": "Mara of the Frontier",
  "background_story": "The administrator-reviewed history.",
  "trait_edits": {},
  "aspects": [],
  "goals": []
}
```

The draft must belong to the authenticated administrator and target the selected
agent. Repeating a successful save for the same draft returns the current agent
without appending a second revision.

### Source-boundary bundles

Scenes retain `DERIVED_FROM` source grouping and deterministic time order. A source is one atomic identity-update and revision boundary. Its scene-local work is partitioned into chunks of at most five scenes, all sharing the same source-start identity. Source bundles run sequentially. ShreckLLM owns provider capacity.

Each chunk has two LLM waves and three normal calls:

1. **Character incorporation** uses `PERSPECTIVE_PROMPT` with the chunk's canonical scenes and identity description. It returns one psychologically distinctive subjective `perspective` and `source_type` per scene. The perspective reflects established personality, values, fears, motivations, and beliefs, and captures supported internal change.
2. **Psychological enrichment** uses `PSYCHOLOGICAL_ANALYSIS_PROMPT` with canonical scene context, the preceding perspective, and identity description. It returns scene-owned emotions, beliefs, and sparse profile events. In parallel, **Trait interpretation** returns directional trait candidates.

After all scene chunks for a source complete, the backend makes one Stage 4 consolidation call if profile events exist. Consolidation deduplicates candidates, reconciles lifecycle and descriptions against current and historical profile items, and selects at most ten focused active aspects and goals. It cites source events; the backend validates IDs, transitions, focus, and provenance. No full-history input is sent. Trait aggregation remains deterministic. The source-level revision becomes the next source's working profile.

Each embodiment generation or JSON-repair request is capped at 10,000 completion tokens. This cap is a provider-cost safeguard; a response stopped for length is rejected as truncated and cannot become a draft result.

### Frontend job-progress contract

`POST /character-agents/embodiment-drafts` still returns `202`:

```json
{
  "draft_id": "9b820ec5-5c1a-4bc6-9b01-4cea280e9420",
  "job_id": 42,
  "status": "queued",
  "draft_url": "/character-agents/embodiment-drafts/9b820ec5-5c1a-4bc6-9b01-4cea280e9420",
  "job_url": "/jobs/42"
}
```

Poll `GET /jobs/42`. `details` is a JSON object when it contains valid JSON. During concurrent chunk analysis, render `active_steps` and `parallel` together:

```json
{
  "id": 42,
  "job_type": "character_agent_embodiment",
  "status": "running",
  "progress": 0.46,
  "details": {
    "stage": "Bundle — session_003_manfred_von_killinger — Step 2: Psychological analysis and trait interpretation",
    "draft_id": "9b820ec5-5c1a-4bc6-9b01-4cea280e9420",
    "bundles": [{
      "index": 3,
      "source_name": "session_003_manfred_von_killinger",
      "status": "processing",
      "active_steps": [2],
      "done_steps": [1],
      "elapsed_seconds": 18.4,
      "chunks": [
        {"index": 1, "scene_count": 5, "status": "processing", "active_steps": [2], "done_steps": [1]},
        {"index": 2, "scene_count": 5, "status": "processing", "active_steps": [2], "done_steps": [1]}
      ],
      "parallel": {
        "active": true,
        "active_branches": ["Psychological analysis and trait interpretation"],
        "active_chunk_count": 2,
        "execution": "shreckllm_managed"
      }
    }]
  }
}
```

Step names are stable: `1` Perspective, `2` Psychological analysis and trait interpretation, and `3` Deterministic source reduction. Render a chunk by its `status`, `scene_count`, `active_steps`, and `done_steps`. `parallel.execution` is `shreckllm_managed`: the frontend must not infer a worker-local concurrency limit. A completed bundle has `done_steps: [1,2,3]`, empty `active_steps`, and `parallel.active: false`. `GET /character-agents/embodiment-drafts/{draft_id}` returns the reviewed result and its point-based trait profile.

### Regeneration and lifecycle safety

Starting an embodiment draft never deletes a CharacterAgent or its history. If the
entity already has an agent, the request must name that exact agent in
`target_character_agent_id`; otherwise the endpoint returns `409`. An active draft
is returned idempotently. A ready or failed draft is retained until an administrator
explicitly confirms replacement with `replace_existing: true`. Draft detail reads
are scoped to their creating administrator. Generation remains a proposal: agent
profile, aspects, goals, and memory are committed only through the reviewed save flow.

The personality contract is intentionally breaking. Drain old workers/queued
jobs, clear old agents and draft payloads, deploy matching consumers,
and regenerate. There is no conversion or compatibility alias. See
[deployment and rollback notes](Dispositional%20Traits.md#breaking-deployment).

This personality contract is intentionally breaking. Drain old workers/queued
jobs, clear old agents and draft payloads, deploy matching consumers,
and regenerate. There is no conversion or compatibility alias. See
[deployment and rollback notes](Dispositional%20Traits.md#breaking-deployment).

### Configuration

- `model_character_agent_character_incorporation`: batch scene perspectives.
- `model_character_agent_scene_interpretation`: narrative identity description
  generation/refresh, per-scene psychological enrichment, and trait interpretation.
- `model_character_agent_deliberation`: v3 query deliberation. `model_character_agent_framing`
  is retained as an unused compatibility setting and should not be configured for new deployments.
- `model_agents_repair_json`: query final repair target.
- Aspect and goal focus are capped at ten each. Historical records and active
  out-of-focus records have no capacity limit; the former capacity settings were
  removed from the configuration API.
- `character_agent_embodiment_debug_artifacts_enabled`: default `true`; writes a
  host-visible local trace for every request under
  `shrecknet/databases/local_test/character_embodiment/`. See
  [debug artifact details](Dispositional%20Traits.md#local-embodiment-debug-artifacts).

Model targets default to empty provider/name until configured or reconciled.
Unrelated belief, emotion, aspect and goal scales retain their existing meanings.
