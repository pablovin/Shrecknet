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

By default, the query uses the CharacterAgent's identity, traits, aspects, and
goals. Set `use_character_identity=False` to use neutral framing and deliberation without
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

draft = await sdk.character_agents.get_embodiment(started.draft_id)
# Poll started.job_id until done, then copy draft.proposal into the form.

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
                name="Frontier leader",
                category="role",
                importance=5,
            )
        ],
    )
)
```

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
        awareness_level=80,
        confidence=70,
        summary="The guard fell at the western gate.",
        interpretation="The keep can no longer protect its own people.",
        memory_strength=90,
        importance=5,
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
        status="believed",
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
`status`, `skip`, and `limit`. The resource also exposes `get`, `update`, and
`delete` methods for perspectives and for each child type.

Generation results only prefill the frontend form. Neo4j is changed only when
the normal `create` call submits the edited aggregate.

Use `response_format.type="json"` with a caller JSON Schema for structured
content. String fields named `rationale` use a server-owned 2,000-character
maximum: longer values are truncated before validation and do not fail the
background job. Graph mutations and raw trait-evidence inspection require an administrator; authenticated users may read/query public agents.

## Dispositional personality

Agent reads expose typed `trait_profile.dispositional_traits` and separate
`trait_profile.steadiness`. Each estimate includes a bounded z estimate, status,
evidence counts and uncertainty. Unknown has null z; midpoint is z=0.
Fetch authoritative constructs, poles and situations with
`await sdk.character_agents.trait_definitions()`; do not maintain separate UI
meaning dictionaries.

```python
from shrecknet_client.character_traits import TraitEdit
from shrecknet_client.models import CharacterAgentUpdate

await sdk.character_agents.update(
    agent.id,
    CharacterAgentUpdate(trait_edits={
        "integrity": TraitEdit(z=1.2, reason="Authored character sheet.")
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
        "integrity": TraitEdit(z=None, reason="Resume evidence-derived estimate.")
    }),
)
```

Embodiment uses chronological source chunks of one source bundle and three normal
LLM calls per bundle, plus authored initialization. Chunks run sequentially. The
server applies the draft's generated profile during creation; submit only changed
z selections in `trait_edits`, with reasons. Evidence continues accumulating
beneath a manual override. STEADINESS is inferred separately from repeated
comparable behavior and never controls query temperature.

This is a breaking contract requiring old CharacterAgents, drafts and checkpoints
to be cleared and regenerated with matching backend/worker/SDK versions. Starting
a draft for an already embodied entity replaces its existing identity. See the
[canonical personality documentation](../../Documentation/Agents/CharacterAgent/Dispositional%20Traits.md)
and [runnable lifecycle example](../examples/10_character_agent/01_dispositional_lifecycle.py).
