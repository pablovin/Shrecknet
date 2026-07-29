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
    "calm_aggressive": 65,
    "cautious_reckless": 30,
    "compassionate_ruthless": 25,
    "trusting_suspicious": 70,
    "honest_deceptive": 40,
    "patient_impulsive": 35,
    "humble_proud": 60,
    "cooperative_dominating": 55,
    "trait_adherence": 80,
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
shreckLLM owns provider retries; Shrecknet
polls the submitted shreckLLM job without a whole-stage deadline.

`generation` contains only `temperature`; removed `generation.mode` and
`generation.max_tokens` fields return `422`.

In structured JSON results, fields named `rationale` use a server-owned
2,000-character maximum. Longer generated values are truncated to 2,000
characters before validation rather than causing repair or job failure.

See [CharacterAgent Query](Query/Query.md) for request and response contracts.

Existing CRUD remains under `/character-agents`, `/character-aspects`, and
`/character-goals`. `trait_adherence` is an integer from 0 through 100, defaults
to 80, and is read as 80 for older graph records where it is absent.
`subtitle` is an optional 255-character identity label. It may be set or cleared
through the existing CharacterAgent patch endpoint; each manual identity edit
creates an immutable revision.

Historical state is available through
`GET /character-agents/{character_agent_id}/revisions` and
`GET /character-agents/{character_agent_id}/identity-changes`. Identity changes
may be filtered with `change_type=axis|subtitle|aspect|goal`.

## Embodiment generation and form creation

All embodiment routes require an administrator and enabled/configured AI agents.

1. `POST /character-agents/embodiment-drafts` with
   `{"ontology_id": 12, "entity_instance_id": "entity-mara"}` returns `202` with
   `draft_id`, `job_id`, `draft_url`, and `job_url`.
2. Poll `GET /jobs/{job_id}` and
   `GET /character-agents/embodiment-drafts/{draft_id}`. Generation states are
   `queued`, `generating`, `ready`, `failed`, and `accepted`.
3. When ready, copy `proposal` into the normal CharacterAgent creation form.
   Edits, additions, and removal of aspects/goals remain frontend-local.
4. Submit the edited aggregate to `POST /character-agents`, including
   `embodiment_draft_id`, optional embedded `aspects`, and optional embedded
   `goals`.

The generated proposal represents axes as an array of objects containing
`axis`, `value`, `justification`, `confidence`, and evidence. The proposal
`name`, `background_story`, and `image_url` are copied deterministically from
the entity rather than generated by the model. The creation form maps each
axis object's `value` to the correspondingly named flat CharacterAgent field;
for example, `{axis: "cautious_reckless", value: 24}` becomes
`"cautious_reckless": 24`. Explanations remain available in the draft for the
review UI but are not CharacterAgent node properties.

`POST /character-agents` creates the complete graph aggregate in one Neo4j
transaction. Draft evidence IDs supplied by embedded aspects/goals must belong
to the referenced draft. After success the draft becomes `accepted` and records
the resulting CharacterAgent ID. Retrying the same draft-backed creation returns
the previously created agent.

Starting another generation replaces the prior unconsumed result for that
entity. The frontend may ignore a result without calling a reject endpoint.

### Re-embodiment (replacing an existing agent)

If the `EntityInstance` already has a CharacterAgent when
`POST /character-agents/embodiment-drafts` is called, the existing agent is
**silently deleted** before the new draft starts. The deletion cascade removes:

- All scene perspectives and their children (emotions, beliefs, impacts)
- All identity revisions and change records (axis history)
- All aspect and goal assignments (orphan definitions are cleaned up)
- The CharacterAgent node itself

The new embodiment generation then proceeds normally. No `409` is returned;
the response is identical to a first-time embodiment. The frontend should
treat this as a re-embodiment flow and update any cached references to the
previous agent ID.

Direct `POST /character-agents` creation also deletes any prior agent for the
same entity before creating the new one, handling any edge case where an agent
exists without an active draft.

Evidence returned in a draft is JSON-safe. Neo4j date/time values nested in
entity properties or Scene provenance are represented as ISO-8601 strings.
Revision 0 uses entity evidence only. Related Scenes are grouped by their
required `DERIVED_FROM` source; groups and Scenes use `created_at` with stable
ID tie-breakers. Each source runs character incorporation, psychological
enrichment, cross-scene observations, then one atomic axes/aspects/goals
update. The first three stages use the generation-start profile snapshot and
may run concurrently across sources. Profile updates always run in source
order against cumulative state. Draft acceptance atomically materializes revisions, change records,
perspectives, reflections, emotion/belief children, and impacts linked to their
active goal/aspect targets.

The profile-update LLM contract uses `behavioural_axis_updates`, a sparse list
of `{axis, delta, justification, confidence, evidence_ids}`. `delta` is a
nonzero signed integer from `-5` through `5`. Omitted axes retain their current
values, and an empty list means no behavioural-axis change. The backend applies
and clamps each delta; generated proposals still expose the existing absolute
`{axis, value, ...}` contract.

Every grounded observation and profile-update list item requires at least one
`evidence_ids` value. Items with missing or empty evidence are omitted
individually, leaving valid siblings intact; a category with no remaining items
is `[]`. Evidence-free subtitle changes are treated as `retain`. Non-empty
unknown or cross-source evidence IDs still fail validation.

The existing background-job response contract is unchanged. During generation,
its details report `status` and `active_stages`: incorporation `[1]`,
psychological enrichment `[2]`, observations `[3]`, atomic profile updates
`[4]`, and validation/merge an empty active-stage array. Concurrent progress
writes are serialized; every source independently reports pending, processing,
done, or failed state and elapsed time. Bundle details add
`checkpointed_stages` and `reused_stages`. On failure, job details add
`failure_category`, `failed_stage`, `failed_source_id`,
`failed_source_alias`, `attempt`, `retryable`, `offending_ids`, and
`allowed_ids`. These fields are additive; successful proposal payloads are
unchanged. The draft `error_message` contains the same concise categorized
diagnostic.

Validated incorporation, interpretation, and observation outputs are
checkpointed per draft revision and source. A retry reuses a checkpoint only
when its prompt version, model target, source evidence, and starting profile
still match. Profile updates always rerun in chronological order. Removing the
checkpoint table rolls back resumability only; existing drafts remain readable
and regenerate normally.

The old manual creation payload remains valid. `embodiment_draft_id`, `aspects`,
and `goals` are optional additions.

Example edited creation form:

```json
{
  "ontology_id": 12,
  "entity_instance_id": "entity-mara",
  "embodiment_draft_id": "draft-123",
  "name": "Mara of the Frontier",
  "background_story": "The final administrator-edited story.",
  "image_url": "https://example.test/mara.png",
  "status": "active",
  "visibility": "private",
  "calm_aggressive": 32,
  "cautious_reckless": 24,
  "compassionate_ruthless": 28,
  "trusting_suspicious": 61,
  "honest_deceptive": 43,
  "patient_impulsive": 27,
  "humble_proud": 58,
  "cooperative_dominating": 56,
  "trait_adherence": 85,
  "aspects": [
    {
      "suggestion_id": "aspect-frontier-leader",
      "name": "Frontier leader",
      "category": "role",
      "description": "Organizes the frontier community.",
      "importance": 5,
      "intensity": 85,
      "justification": "Mara repeatedly organizes and leads the settlement.",
      "evidence_ids": ["milestone:milestone-31"],
      "confidence": 0.92
    }
  ],
  "goals": [
    {
      "suggestion_id": "goal-protect-community",
      "title": "Protect the frontier community",
      "description": "Keep the settlement safe.",
      "goal_type": "obligation",
      "status": "active",
      "priority": 90,
      "commitment": 88,
      "justification": "Mara explicitly accepts responsibility for the settlement's safety.",
      "basis": "explicit",
      "evidence_ids": ["milestone:milestone-31"],
      "confidence": 0.9
    }
  ]
}
```

`suggestion_id`, `justification`, `evidence_ids`, `confidence`, and goal `basis`
are optional for manually added form items. Generated behavioural axes, aspects,
and goals always include `justification` and `confidence`. When evidence IDs are supplied with
`embodiment_draft_id`, every ID must occur in that draft.

Configuration:

- `model_character_agent_framing` and `model_character_agent_deliberation`:
  query pipeline targets.
- `model_agents_repair_json`: shared JSON repair target used for the optional
  single repair attempt.
- `model_character_agent_character_incorporation`: perspectives and expressive reflections.
- `model_character_agent_scene_interpretation`: psychological enrichment and observations.
- `model_character_agent_update`: one atomic axis, aspect, and goal update.
- Every CharacterAgent model target defaults to
  `{"provider": "", "name": ""}`. Empty targets remain empty until an
  administrator selects a target or enabling AI agents reconciles them against
  the available shreckLLM providers.
- Startup prewarming covers configured CharacterAgent targets on providers that
  ShreckLLM reports operational. Each job performs only a cached-status
  preflight; an explicit provider test is not repeated before generation.
- `character_agent_embodiment_evidence_tokens`: bounded evidence budget; default `12000`.
- `character_agent_embodiment_max_aspects`: default `12`.
- `character_agent_embodiment_max_goals`: default `8`.
- `character_agent_embodiment_source_concurrency`: concurrent source snapshot
  analyses; default `4`, allowed range `1` through `16`. Profile updates remain
  sequential regardless of this value. Use `1` to reduce provider load.
- `character_agent_embodiment_semantic_correction_attempts`: targeted retries
  for structurally valid output containing invalid evidence references; default
  `1`, allowed range `0` through `3`. Set `0` for immediate strict failure.
