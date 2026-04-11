# Event Node (`Event`)

`Event` is the primary representation unit in Shrecknet v0.3.

## Purpose

Each event represents a meaningful state transition in the world timeline.

Examples:

- A treaty is signed.
- A character dies.
- A faction splits.
- A discovery triggers a new arc.

## Core Event Properties

Typical fields stored on `Event`:

- `event_id` (unique identifier)
- `instance_id` (owner `OntologyInstance`)
- `ontology_id` (ontology scope)
- `title`
- `description`
- `source_entity_id` (optional)
- `involves_entity_ids` (optional denormalized list)
- `relations` (denormalized relation summary for API projection)
- `created_at`, `updated_at`, `last_updated_date`
- embedding lifecycle fields (`is_embedded`, `last_embedded_date`)

## Event Participation Edges

- `(Event)-[:SOURCE_ENTITY]->(EntityInstance)`
- `(Event)-[:INVOLVES_ENTITY]->(EntityInstance)`

These encode who originates and who participates in the event.

## Event-to-Event Relations (v0.3 fixed set)

- `BEFORE`
  - `A BEFORE B` means A happens earlier than B.
- `AFTER`
  - `A AFTER B` means A happens later than B.
  - Temporal inverse with `BEFORE` is enforced in write flows.
- `DERIVED_FROM`
  - Event A is derived from event B (narrative or structural derivation).
- `RELATED_TO`
  - Non-temporal semantic relation between events.

## Design Notes

- Temporal traversal should rely on graph edges, not pointer fields.
- Event chains are intended to be query-first for retrieval and generation.
- `BEFORE`/`AFTER` cycles are invalid and checked during migration validation.
