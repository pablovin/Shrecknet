# Scene-Centric Memory

Shrecknet now uses a scene-centric temporal model as the canonical representation for world memory updates.

Related embedding documentation:
- [Scene-Centric Embedding](./SCENE_EMBEDDING.md)

## Concept

A Scene is a bounded narrative segment inside one ontology instance. It groups the timeline anchors that define what starts, evolves, and closes in that segment.

A Milestone is a temporal anchor inside a Scene. Milestones carry ordering, temporal semantics, and links to involved entities.

This model makes temporal memory explicit and queryable without depending on legacy Event writes.

## Core Node Types

### Scene

A Scene:
- Belongs to one ontology instance (`(:OntologyInstance)-[:HAS_SCENE]->(:Scene)`)
- Has one provenance link via `derived_from.entity_instance_id`
- Contains a sequence of Milestones

Required high-level constraints:
- Must contain at least 2 milestones
- Must contain exactly one `boundary_type = begin`
- Must contain exactly one `boundary_type = end`

### Milestone

A Milestone:
- Belongs to exactly one Scene (`(:Scene)-[:CONTAINS]->(:Milestone)`)
- Has one provenance link via `derived_from.entity_instance_id`
- Can have typed links to entities via `RELATES_TO`
- Can be ordered by `FOLLOWED_BY` / `PRECEDED_BY` and local order metadata

## Relationship Map

- OntologyInstance to Scene:
  - `HAS_SCENE`
- Scene to Milestone:
  - `CONTAINS`
- Milestone to Milestone:
  - `FOLLOWED_BY`
  - `PRECEDED_BY`
- Scene or Milestone to Entity instance:
  - `DERIVED_FROM` (single provenance anchor)
- Milestone to Entity instance:
  - `RELATES_TO` (typed involvement relation)

## Legacy Event Policy

Legacy Event nodes remain readable for compatibility, but canonical write flows should target Scene and Milestone endpoints only.

## Why This Matters

- Better temporal consistency for each narrative segment
- Explicit begin/end boundaries for robust timeline traversal
- Cleaner entity anchoring and provenance tracking
- Safer migration path from event-centric writes to scene-centric persistence
