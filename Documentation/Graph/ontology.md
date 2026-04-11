# Ontology in Event-Centric Shrecknet

In Shrecknet v0.3, ontology still defines the domain vocabulary, but runtime memory is event-centric.

## Role of Ontology

Ontology definitions (SQL) specify:

- Entity definitions (`ontology_entities`)
- Property definitions (`entity_properties`)
- Relationship definitions (`entity_relationships`)

This definition layer validates and constrains runtime graph writes.

## Runtime Implementation

Runtime graph data (Neo4j) is organized under `OntologyInstance` nodes:

- `OntologyInstance` groups data for one world-instance slice.
- Entities and events both attach to the same instance:
  - `HAS_ENTITY`
  - `HAS_EVENT`

This preserves ontology scoping while enabling event-first reasoning.

## Validation and Constraints

Ontology definitions are used by backend services to:

- Validate entity payloads and relationship definition IDs.
- Enforce allowed entity-to-entity relationship cardinality and target definitions.
- Keep runtime graph aligned with ontology schema.

## Practical outcome

- Ontology remains the **schema authority**.
- Neo4j events become the **memory authority** for chronology and narrative state transitions.
