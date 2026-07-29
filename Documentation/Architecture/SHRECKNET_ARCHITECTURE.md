# Shrecknet Architecture

Shrecknet is a scene-centric memory system for storytelling where ontology, graph, and knowledge layers evolve together.

## Diagram

![Shrecknet Architecture](./assets/shrecknet-architecture.png)

## Reading the Diagram

1. Ontology layer
   Defines entity types, properties, and relationships that describe the world schema.

2. Graph layer
   Connects entity instances with scenes and milestones so chronology, causality, and continuity are queryable.

3. Knowledge layer
   Stores narrative text and embeddings used by retrieval and generation agents.

4. Agent layer
   Elder, Librarian, Architect, and Novelist operate on shared memory state to answer, retrieve, evolve, and narrate.

## Scene-Centric Interpretation

Scenes and milestones are first-class records, not only annotations. New observations can:

- append scenes and milestones,
- update entities,
- and trigger ontology evolution when the schema needs to expand.

This enables long-running campaigns to accumulate memory incrementally without flattening history.
## Character identity chronology

CharacterAgent embodiment is a source-boundary projection pipeline. SQL stores
the reviewable generated timeline; acceptance atomically materializes the
current CharacterAgent, immutable `CharacterIdentityRevision` snapshots,
`CharacterIdentityChange` provenance, and ScenePerspectives in Neo4j. Revision
0 contains entity evidence only, preventing later narrative facts from leaking
into earlier perspectives.

CharacterAgent queries use durable SQL `BackgroundJob` records and the
`character_agent` Celery queue. The HTTP submission validates access and
returns immediately; the worker reloads current graph identity, runs compact
framing and deliberation stages, and records safe stage progress and terminal
output. shreckLLM owns provider retries, while Shrecknet polls each submitted
shreckLLM job without a pipeline-level HTTP deadline.

## Agent model targets

Every agent-stage model setting uses the same `LLMModelTarget` contract:

```json
{"provider": "openrouter", "name": "anthropic/claude-sonnet-4", "reasoning": false}
```

`reasoning` is editable with the provider and model through `GET/PUT /config/`
and is advertised to configuration frontends by `GET /config/schema`. It
defaults to `false` for existing and new targets. Shrecknet includes the value
in every shreckLLM chat request. shreckLLM maps it to a provider-native
parameter when the selected adapter supports one (currently OpenRouter's
`reasoning.enabled`, with `reasoning.effort: "high"` when enabled) and otherwise
ignores it. Changing the value is a hot configuration update and requires no
persistence migration or service restart.
