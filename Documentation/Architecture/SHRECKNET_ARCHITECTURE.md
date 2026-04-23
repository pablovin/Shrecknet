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
