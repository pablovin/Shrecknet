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
    "image_url": null,
    "status": "active",
    "visibility": "public",
    "trait_profile": {
      "version": "dispositions-v1",
      "dispositional_traits": {
        "integrity": {"z": null, "status": "unknown", "qualifying_count": 0, "observation_ids": [], "uncertainty": [], "accepted_count": 0, "comparison_start": 0, "applied_source_ids": []},
        "caution": {"z": null, "status": "unknown", "qualifying_count": 0, "observation_ids": [], "uncertainty": [], "accepted_count": 0, "comparison_start": 0, "applied_source_ids": []},
        "presence": {"z": null, "status": "unknown", "qualifying_count": 0, "observation_ids": [], "uncertainty": [], "accepted_count": 0, "comparison_start": 0, "applied_source_ids": []},
        "forbearance": {"z": null, "status": "unknown", "qualifying_count": 0, "observation_ids": [], "uncertainty": [], "accepted_count": 0, "comparison_start": 0, "applied_source_ids": []},
        "diligence": {"z": null, "status": "unknown", "qualifying_count": 0, "observation_ids": [], "uncertainty": [], "accepted_count": 0, "comparison_start": 0, "applied_source_ids": []},
        "curiosity": {"z": null, "status": "unknown", "qualifying_count": 0, "observation_ids": [], "uncertainty": [], "accepted_count": 0, "comparison_start": 0, "applied_source_ids": []},
        "sharing": {"z": null, "status": "unknown", "qualifying_count": 0, "observation_ids": [], "uncertainty": [], "accepted_count": 0, "comparison_start": 0, "applied_source_ids": []},
        "restlessness": {"z": null, "status": "unknown", "qualifying_count": 0, "observation_ids": [], "uncertainty": [], "accepted_count": 0, "comparison_start": 0, "applied_source_ids": []}
      },
      "steadiness": {"z": null, "status": "unknown", "qualifying_count": 0, "observation_ids": [], "uncertainty": [], "accepted_count": 0, "comparison_start": 0, "applied_source_ids": []},
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
      "status": "active",
      "obtained_from_scene_id": null,
      "id": "aspect-31",
      "created_at": "2026-07-25T09:05:00Z",
      "updated_at": "2026-07-25T09:05:00Z"
    },
    "importance": 5,
    "intensity": 85,
    "notes": null,
    "status": "active",
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
    "ontology_id": 12,
    "title": "Protect the villagers",
    "description": "Keep the settlement safe without abandoning its people.",
    "goal_type": "obligation",
    "status": "active",
    "priority": 95,
    "commitment": 90,
    "obtained_from_scene_id": null,
    "id": "goal-17",
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
- Equivalent nested CRUD under `beliefs` and `impacts`

The perspective list accepts `status`, `skip`, and `limit`, returns all statuses
unless filtered, and contains lightweight perspective records. The detail
response embeds ordered `emotions`, `beliefs`, and `impacts`.

Example perspective creation:

```json
{
  "scene_id": "scene-31",
  "source_type": "witnessed",
  "awareness_level": 80,
  "confidence": 70,
  "summary": "The guard fell at the western gate.",
  "interpretation": "The keep can no longer protect its own people.",
  "character_reflection": "I can still hear the gate splintering. We were never safe there.",
  "memory_strength": 90,
  "importance": 5,
  "status": "active"
}
```

Example emotion, belief, and impact payloads:

```json
{"arousal": 85, "valence": 2, "description": "Angry and frustrated."}
```

```json
{"statement": "Lancelot killed the guard.", "confidence": 60, "status": "believed"}
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
Embodiment-draft acceptance preserves goals and aspects referenced by historical
timeline impacts even when they are absent from the final profile. Their
`PURSUES` or `HAS_ASPECT` assignment is stored with `status=inactive`; final
profile assignments remain active.

## Query

`POST /character-agents/{character_agent_id}/query` requires authentication and
enabled AI agents and returns `202` with a background job ID and `status_url`.
Poll `GET /character-agents/{character_agent_id}/query-jobs/{job_id}` until its
status is `done` or `failed`. Non-administrators may submit and read only their
own jobs for public agents; administrators may use public or private agents.
The agent must be active.

`use_character_identity` defaults to `true` and normally performs two LLM calls:
compact identity framing followed by deliberation/rendering. Invalid final JSON
may receive one repair attempt through `model_agents_repair_json`. Generic mode
also performs framing followed by deliberation, but neither call receives
CharacterAgent identity. Generic framing receives only the query and context.
Identity framing keeps active selector IDs, resolves exact unambiguous
aspect/goal names to their active IDs, and discards unknown or ambiguous
selectors instead of failing the query.
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
`trait_profile.steadiness`. Every slot has z, bounded z estimate, status, evidence
counts/references, and uncertainty. Unknown is null, not z=0. See the complete
[trait specification and policy](Dispositional%20Traits.md).

Authenticated metadata: `GET /character-agents/trait-definitions` returns the
registry and scale mapping. The percentile reference is the general human population.

Administrator create/PATCH inputs use z edits rather than raw profiles:

```json
{
  "trait_edits": {
    "integrity": {"z": 1.2, "reason": "Authored character sheet."},
    "sharing": {"z": -0.7, "reason": "Scrupulous but ungenerous."}
  }
}
```

Each slot accepts a finite number from -1.9 through 1.9. Omitted slots are retained. A nonblank
reason is required. `{"trait_edits":{"integrity":{"z":null,"reason":"Resume evidence."}}}`
clears that manual override and restores the current inferred value, possibly unknown.
Raw z, caller-invented evidence, unknown trait names, and removed personality fields
are not accepted as edits (`422`). Graph mutation remains administrator-only.
Manual values stay effective while underlying evidence accumulates.

`GET /character-agents/{agent_id}/revisions` returns immutable profile snapshots,
batch IDs and scene IDs. `GET /character-agents/{agent_id}/identity-changes` accepts
`change_type=trait|steadiness|subtitle|aspect|goal`. Changes include previous/new
estimate objects, justification, canonical evidence IDs, observation IDs, policy
version, and actor ID for manual changes.

`GET /character-agents/{agent_id}/trait-evidence` is administrator-only. Filters:
`trait` (directional key), `revision` (inclusive cutoff), `skip` (default 0), and
`limit` (default 100, maximum 500). It returns structured observations with
eligibility/exclusion reasons; it includes weak and no-change evidence. Raw trait
observations are not included in public revision responses. Visibility rules for
existing profile and history reads continue to apply.

## Embodiment generation and reviewed creation

1. Administrator submits `POST /character-agents/embodiment-drafts` with
   `{"ontology_id":12,"entity_instance_id":"entity-mara"}`. AI agents must be
   enabled and configured. Response `202` includes draft/job IDs and polling URLs.
2. Poll `GET /jobs/{job_id}` and
   `GET /character-agents/embodiment-drafts/{draft_id}`. States remain `queued`,
   `generating`, `ready`, `failed`, and `accepted`.
   When the selected LLM provider or model is unavailable, a failed draft's
   `error_message` names the provider, selected model, and safe availability
   reason, then instructs the administrator to configure an available model and
   retry. Job details additionally use `failure_category: "provider_unavailable"`.
3. Review `proposal.trait_profile`, aspects/goals, source evidence and timeline.
   The server-owned profile is the inference baseline; do not submit that entire
   read object as a write payload.
4. Submit the normal creation aggregate with the draft ID and any manual z
   edits. Identical z values preserve inferred provenance; different z values produce
   a final manual revision after the generated timeline.

```json
{
  "ontology_id": 12,
  "entity_instance_id": "entity-mara",
  "embodiment_draft_id": "draft-123",
  "name": "Mara of the Frontier",
  "background_story": "The administrator-reviewed history.",
  "visibility": "private",
  "trait_edits": {"integrity":{"z":1.2,"reason":"Reviewed authored characterization."}},
  "aspects": [],
  "goals": []
}
```

Creation materializes the graph aggregate atomically. Embedded aspect/goal
provenance must belong to the draft. Retrying an accepted draft returns its
existing agent. Manual creation without a draft starts unobserved slots as unknown.
Name/story/image derivation continues to use canonical entity information.

### Source-boundary bundles

Scenes retain `DERIVED_FROM` source grouping and deterministic time order. All
scenes from a source are one atomic bundle; the backend never splits, truncates,
or drops them for a count or local input budget. Each bundle runs three normal
calls: perspectives, perspective-only per-scene enrichment (including scene-local
trait candidates and durable aspect/goal signals), and one cumulative profile
proposal. Only incorporation receives the raw scene; enrichment receives the
grounded character perspective without its presentation-only reflection. The
backend validates and grounds the candidates before the profile proposal; it does
not make a separate cross-scene-observation call. A scene has no hard cap on trait
candidates, while aspect and goal signals are limited to one each and never mutate
state on their own. Baseline authored extraction adds one initial call. No scenes
are required for authored-only initialization. All bundle perspectives use its
starting revision; the updated identity takes effect at bundle end.

Every enrichment item explicitly returns its six arrays: `emotions`, `beliefs`,
`impacts`, `trait_candidates`, `aspect_signals`, and `goal_signals`. Empty arrays
are valid; omitted arrays are rejected and receive one schema-correction attempt.
Impacts can only target existing profile aspect/goal IDs, so an agent with neither
can correctly have `impacts: []` while still emitting candidate or durable-signal
evidence for later profile creation.

Outputs contain per-scene provenance and availability cutoffs. Invalid references,
missing/duplicate/reordered scene outputs, or unsupported updates fail validation
and may use the configured bounded correction. Explicit future citations are
rejected; shared-context prompts cannot guarantee absence of uncited hindsight.

Checkpoints include the preceding profile/evidence, complete source inputs,
versions, and model targets. Architect uses the same accumulation/timeline rules
for new scenes. Stale concurrent writes return a conflict; edited, removed, or
backdated processed history requires regeneration. Background failure is reported
through the existing job/draft error path.

### Regeneration and rollout

Starting an embodiment draft for an already embodied entity deletes its current
CharacterAgent aggregate through the existing cleanup path, then generates a new
draft. Its perspectives, history, and unshared aspect/goal definitions are removed;
canonical entities/scenes remain. This endpoint is not a nondestructive preview.

This personality contract is intentionally breaking. Drain old workers/queued
jobs, clear old agents and draft/checkpoint payloads, deploy matching consumers,
and regenerate. There is no conversion or compatibility alias. See
[deployment and rollback notes](Dispositional%20Traits.md#breaking-deployment).

### Configuration

- `model_character_agent_character_incorporation`: batch perspectives/reflections.
- `model_character_agent_scene_interpretation`: authored baseline and per-scene
  psychological enrichment/candidate extraction.
- `model_character_agent_update`: cumulative trait proposals, aspects and goals.
- `model_character_agent_framing` / `model_character_agent_deliberation`: query stages.
- `model_agents_repair_json`: query final repair target.
- `character_agent_embodiment_evidence_tokens`: default 12000; applies only to
  legacy evidence previews, never to an embodiment source bundle.
- `character_agent_embodiment_max_aspects` / `character_agent_embodiment_max_goals`:
  defaults 12/8, active capacities. Per-bundle operation caps remain two aspects/one goal.
- `character_agent_embodiment_semantic_correction_attempts`: default 1, range 0–3.
- `character_agent_embodiment_debug_artifacts_enabled`: default `true`; writes a
  host-visible local trace for every request under
  `shrecknet/databases/local_test/character_embodiment/`. See
  [debug artifact details](Dispositional%20Traits.md#local-embodiment-debug-artifacts).

Model targets default to empty provider/name until configured or reconciled.
Unrelated belief, emotion, aspect and goal scales retain their existing meanings.
