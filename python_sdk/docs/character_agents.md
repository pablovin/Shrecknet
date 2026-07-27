# CharacterAgent queries

```python
from shrecknet_client import Shrecknet
from shrecknet_client.models import CharacterAgentQueryRequest

async with Shrecknet(token="...") as sdk:
    response = await sdk.character_agents.query(
        "character-agent-id",
        CharacterAgentQueryRequest(
            query="Write a brief in-character reply to the accusation."
        ),
    )
    print(response.content)
```

By default, the query uses the CharacterAgent's identity, traits, aspects, and
goals. Set `use_character_identity=False` to make one generic LLM call without
sending CharacterAgent profile data:

```python
response = await sdk.character_agents.query(
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
content. The API remains admin-only in Phase 1.
