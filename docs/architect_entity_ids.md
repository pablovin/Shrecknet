# Architect Entity Instance IDs

## Overview

When the Architect agent analyzes text in **Step 1**, it creates proposals that can reference entities in two ways:

1. **NEW_INSTANCE proposals**: Suggest creating a new entity with a given `alias`
2. **UPDATE_INSTANCE proposals**: Suggest updating an existing entity identified by `entity_instance_id`

## Entity Instance ID Field

The `entity_instance_id` field is a UUID that uniquely identifies an entity instance in the knowledge graph.

### Where does it come from?

In Step 1, when the Architect analyzes text chunks, it:
1. Performs semantic retrieval to find similar existing entities across the **entire knowledge graph** (not just the current ontology instance)
2. The LLM identifies entities from the retrieval results that should be updated
3. These entities' `entity_instance_id` values are included in UPDATE_INSTANCE proposals

### Important Notes

- **Cross-Instance References**: The `entity_instance_id` can refer to entities from ANY ontology instance in the graph, not just the current one being analyzed
- **Available in Step 1**: The frontend receives these IDs in the proposal data from Step 1
- **Step 2 Processing**: In Step 2, the backend loads these entities (even if they're from other instances) to apply updates

## Frontend Access

The `entity_instance_id` is available in the proposal object returned from Step 1:

```typescript
type ArchitectProposal = {
  id: string;
  proposal_type: "new_instance" | "update_instance";
  status: "pending" | "approved" | "rejected";
  entity_definition_id: number | null;
  entity_instance_id: string | null;  // ← Available here for UPDATE_INSTANCE proposals
  alias: string | null;
  confidence: number | null;
  justification: string | null;
  // ... other fields
};
```

### Usage in Frontend

For UPDATE_INSTANCE proposals:
- Display `entity_instance_id` to help users identify which entity will be updated
- The `alias` field (if available) provides a human-readable name
- Users can approve/reject or modify the target entity via `corrected_entity_instance_id`

## Correcting Entity References

Users can redirect an update to a different entity by providing:
- `corrected_entity_instance_id`: Override which entity to update
- `corrected_proposal_type`: Change from UPDATE_INSTANCE to NEW_INSTANCE (or vice versa)

Example validation payload:
```json
{
  "proposal_id": "...",
  "status": "approved",
  "corrected_entity_instance_id": "different-entity-uuid"
}
```

## Technical Details

### Step 1 Data Flow
1. Retrieval finds entities with their IDs
2. LLM identifies which entities to update
3. Proposals include `entity_instance_id` from retrieval results

### Step 2 Data Flow
1. Backend loads entities from the current ontology instance
2. **NEW**: If proposal references an entity not in current instance, it's loaded from the graph
3. Entity updates are applied using the LLM-extracted properties/relationships

This ensures that proposals can reference any entity in the knowledge graph while still being processed correctly.
