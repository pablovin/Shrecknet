# Shrecknet Event-Centric Memory (v0.3)

Shrecknet now models memory as an **event-first knowledge graph**.

Instead of treating entities as the primary memory unit, the graph treats each meaningful state transition as an `Event` node. Narrative progression is represented explicitly through event-to-event relations, while entities participate in events through dedicated participation edges.

## Core Principle

- **Events are first-class memory units**.
- **Entities provide context and participation**, not chronology.
- **Reasoning and traversal follow event chains**.

## High-Level Graph Shape

- `OntologyInstance` owns runtime graph slices.
- `EntityInstance` stores entity identity and descriptive properties.
- `Event` stores what happened and how it connects to other events.

Primary structural links:

- `(OntologyInstance)-[:HAS_ENTITY]->(EntityInstance)`
- `(OntologyInstance)-[:HAS_EVENT]->(Event)`
- `(Event)-[:SOURCE_ENTITY]->(EntityInstance)`
- `(Event)-[:INVOLVES_ENTITY]->(EntityInstance)`

Event-to-event relations (strict v0.3 set):

- `BEFORE`
- `AFTER`
- `DERIVED_FROM`
- `RELATED_TO`

## Why this model

- Makes temporal flow explicit and traversable.
- Improves causality and sequence-aware retrieval.
- Separates “what exists” (entities) from “what happened” (events).
- Supports cleaner generation pipelines (novelist/architect) over event chains.

## API Direction

Event endpoints are now modeled under `/ontology-instances/{instance_id}/events` with event-centric payloads.

## Related docs

- [ontology.md](./ontology.md)
- [event.md](./event.md)
- [entity.md](./entity.md)
