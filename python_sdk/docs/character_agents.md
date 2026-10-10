# CharacterAgent queries

```python
from shrecknet_client import Shrecknet
from shrecknet_client.models import CharacterAgentQueryRequest

async with Shrecknet(token="...") as sdk:
    queued = await sdk.character_agents.query(
        "character-agent-id",
        CharacterAgentQueryRequest(
            query="Write a brief in-character reply to the accusation."
        ),
    )
    job = await sdk.character_agents.wait_for_query(
        "character-agent-id", queued.job_id
    )
    if job.status == "failed":
        raise RuntimeError(job.error.message)
    print(job.result.content)
    print(job.result.decision_basis)
```

By default, the query uses the CharacterAgent's persisted `identity_description`
(`identity_summary`, `psychological_summary`, and qualitative
`personality_traits`) as its sole personality input. Its authored background,
numerical traits, STEADINESS, aspects, and goals are not sent. Queries use one
deliberation call with deterministic retrieval of the queried character's own
scene perspectives, ranked from `query` and `context` alone; no other character
or canonical scene memory is supplied. If the identity description is absent,
the job fails with `error.code="identity_unavailable"`. Set
`use_character_identity=False` to use neutral
single-call deliberation without
sending CharacterAgent profile data:

```python
queued = await sdk.character_agents.query(
    "character-agent-id",
    CharacterAgentQueryRequest(
        query="Give a neutral assessment of the accusation.",
        use_character_identity=False,
    ),
)
```

The referenced CharacterAgent must still be visible to the caller and active.
The public `generation` object contains only `temperature`. CharacterAgent query
calls do not send an explicit output token cap to shreckLLM.

## Administrator embodiment workflow

```python
from shrecknet_client.models import (
    CharacterAgentCreateRequest,
    CharacterAgentEmbeddedAspect,
    EmbodimentDraftCreate,
)

started = await sdk.character_agents.start_embodiment(
    EmbodimentDraftCreate(ontology_id=12, entity_instance_id="entity-mara")
)

# Drafts remain discoverable after the initiating client disconnects.
recent = await sdk.character_agents.list_embodiments(12)

draft = await sdk.character_agents.get_embodiment(started.draft_id)
# Poll started.job_id until done, then copy draft.proposal into the form.

# Remove a completed proposal from the review queue after it is no longer needed.
await sdk.character_agents.delete_embodiment(draft.id)

agent = await sdk.character_agents.create(
    CharacterAgentCreateRequest(
        ontology_id=12,
        entity_instance_id="entity-mara",
        embodiment_draft_id=draft.id,
        name="Edited name",
        subtitle="The Archivist of Arkham",
        background_story="Edited final story",
        aspects=[
            CharacterAgentEmbeddedAspect(
                name="I lead the frontier settlement",
                category="role",
                status="active",
                in_focus=True,
            )
        ],
    )
)
```

Embodiment drafts can also be managed through the HTTP API:
`GET /character-agents/embodiment-drafts?ontology_id={ontology_id}&limit=20`
lists the current administrator's drafts, and
`DELETE /character-agents/embodiment-drafts/{draft_id}` removes an owned draft
with status `204`. Only `ready` and `failed` drafts can be deleted; queued,
generating, and accepted drafts return `409`. The associated background-job
record remains available through `GET /jobs/{job_id}`.

```python
from shrecknet_client.models import CharacterAgentUpdate

await sdk.character_agents.update(agent.id, CharacterAgentUpdate(subtitle="The Doll"))
revisions = await sdk.character_agents.list_revisions(agent.id)
subtitle_changes = await sdk.character_agents.list_identity_changes(
    agent.id, change_type="subtitle"
)
```

## Scene perspectives

Administrators can create and maintain a subjective projection without changing
the canonical scene:

```python
from shrecknet_client.models import (
    CharacterBeliefCreate,
    CharacterImpactCreate,
    EmotionalInterpretationCreate,
    ScenePerspectiveCreate,
)

perspective = await sdk.character_agents.create_perspective(
    agent.id,
    ScenePerspectiveCreate(
        scene_id="scene-31",
        source_type="witnessed",
        perspective="The keep can no longer protect its people. I fear we were not ready.",
    ),
)

await sdk.character_agents.create_emotion(
    agent.id,
    perspective.id,
    EmotionalInterpretationCreate(
        arousal=85,
        valence=2,
        description="Angry and frustrated.",
    ),
)
await sdk.character_agents.create_belief(
    agent.id,
    perspective.id,
    CharacterBeliefCreate(
        statement="Lancelot killed the guard.",
        confidence=60,
    ),
)
await sdk.character_agents.create_impact(
    agent.id,
    perspective.id,
    CharacterImpactCreate(
        impact_type="goal_change",
        direction="advanced",
        magnitude=80,
        description="The confession strengthens the need for justice.",
        target_id="goal-17",
        caused_by_milestone_id="milestone-91",
    ),
)
```

`get_perspective()` returns the nested aggregate. `list_perspectives()` supports
`skip` and `limit`. A perspective contains `source_type` and one `perspective`
text field. The resource also exposes `get`, `update`, and
`delete` methods for perspectives and for each child type.

Aspect and goal assignment lifecycle/focus is character-specific. Use
`list_aspects`, `assign_aspect`, and `update_aspect_assignment`, plus
`list_goals`, `pursue_goal`, and `update_goal_assignment`. Assignment writes
accept `status` and `in_focus`; each category permits at most ten focused active
records. Definitions can be shared without sharing lifecycle or focus.

Generation results only prefill the frontend form. Neo4j is changed only when
the normal `create` call submits the edited aggregate.

Use `response_format.type="json"` with a caller JSON Schema for structured
content. String fields named `rationale` use a server-owned 2,000-character
maximum: longer values are truncated before validation and do not fail the
background job. Graph mutations and raw trait-evidence inspection require an administrator; authenticated users may read/query public agents.

## Dispositional personality

Agent reads expose typed `trait_profile.dispositional_traits` and separate
`trait_profile.steadiness`. Each estimate includes an integer `point` from 1 to 9 or null, status,
`observation_count`, and `observation_ids` referring to `ScenePerspective.id`.
Unknown has null point; point 5 with opposing evidence is mixed, not automatically average.
Fetch authoritative constructs, poles and situations with
`await sdk.character_agents.trait_definitions()`; do not maintain separate UI
meaning dictionaries.

If an agent has a stored trait profile from an obsolete format, list and detail
reads return an all-unknown profile and set
`trait_profile_requires_regeneration=True`. This preserves access to the agent
for inspection and deletion without converting or rewriting its stored data.
Operations that require current trait estimates can still return `409` until the
profile is regenerated.

```python
from shrecknet_client.character_traits import TraitEdit
from shrecknet_client.models import CharacterAgentUpdate

await sdk.character_agents.update(
    agent.id,
    CharacterAgentUpdate(trait_edits={
        "integrity": TraitEdit(point=7, reason="Authored character sheet.")
    }),
)
evidence = await sdk.character_agents.list_trait_evidence(
    agent.id, trait="integrity", revision=3
)
changes = await sdk.character_agents.list_identity_changes(agent.id, change_type="trait")

# Clear the override; explicit null must be transmitted.
await sdk.character_agents.update(
    agent.id,
    CharacterAgentUpdate(trait_edits={
        "integrity": TraitEdit(point=None, reason="Resume evidence-derived estimate.")
    }),
)
```

Embodiment processes source bundles chronologically. A source remains one
identity-update/revision boundary, and its ordered scenes are divided into
chunks of at most five. Chunks run perspective extraction, then psychological
enrichment and trait interpretation in parallel. After every chunk completes,
the server makes one optional Stage 4 consolidation call if profile events were
found. A source with `n` chunks therefore makes `3n` scene-stage calls plus at
most one consolidation call, in addition to identity and bounded recovery calls.
Consolidation compares source-local events with current and historical profile
items, updates character-specific lifecycle/focus, and selects at most ten
focused active aspects and goals. It does not receive full scene history.
Emotions and beliefs remain linked to their originating perspective; beliefs
are historical snapshots without lifecycle status. The server applies the draft's generated profile during
creation; submit only changed point selections in `trait_edits`, with reasons.
Evidence continues accumulating beneath a manual override. STEADINESS is inferred
separately from repeated comparable behavior and never controls query temperature.

This is a breaking contract requiring trait evidence and profiles to be
regenerated from source material with matching backend/worker/SDK versions.
Old z-format trait data is not accepted by the point-based runtime. Starting
a draft for an already embodied entity replaces its existing identity. See the
[canonical personality documentation](../../Documentation/Agents/CharacterAgent/Dispositional%20Traits.md)
and [runnable lifecycle example](../examples/10_character_agent/01_dispositional_lifecycle.py).


Embodiment validation uses one shared backend replacement budget configured by
`character_agent_embodiment_validation_retries` (default 1, range 0–3). JSON,
schema, empty-body, and semantic failures share this budget; they do not obtain
separate repair attempts. A failed source cannot publish an invalid profile
update. Public SDK draft models, timeline operations, and numeric event references
are unchanged. See the [canonical validation and deployment contract](../../Documentation/Agents/CharacterAgent/CharacterAgent.md#embodiment-validation-and-recovery)
for configuration aliases, categorized failures, and worker rollout requirements.
