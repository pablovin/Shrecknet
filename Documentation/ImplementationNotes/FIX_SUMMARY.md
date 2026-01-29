# Fix Summary: Entity Instance ID Not Found Error

## Problem

The backend was giving errors when processing Architect step 2 (entity generation):

```
ValueError: Entity instance f172760c-178c-4b4d-bd27-024e1a84d97e not found
```

This happened when the frontend sent entity instance IDs from step 1 proposals to be updated in step 2.

## Root Cause

In **Step 1** (architect_analysis):
- The Architect analyzes text and performs semantic retrieval
- Retrieval searches across the **entire knowledge graph** (all ontology instances)
- When the LLM identifies existing entities to update, it uses their `entity_instance_id` from the retrieval results
- These IDs can refer to entities from **any instance**, not just the current one

In **Step 2** (architect_generation):
- The code loaded only entities from the **current ontology instance** into `existing_entities_map`
- When a proposal referenced an entity from a different instance, it wasn't found
- This caused the "Entity instance not found" error

## Solution

Modified `backend/app/tasks/architect_generation.py` to:

1. Build the initial `existing_entities_map` from the current instance (unchanged)
2. **NEW**: Check if any proposals reference entities not in the map
3. **NEW**: Load missing entities directly from the Neo4j graph using their `entity_instance_id`
4. **NEW**: Add them to the map so they can be updated

Code change summary:
```python
# After building map from current instance...

# Check for missing entities
missing_entity_ids = set()
for proposal in update_proposals:
    entity_id = proposal.get("entity_instance_id")
    if entity_id and entity_id not in existing_entities_map:
        missing_entity_ids.add(entity_id)

# Load missing entities from graph
if missing_entity_ids:
    logger.info("Loading %d missing entities from graph", len(missing_entity_ids))
    for entity_id in missing_entity_ids:
        # Query Neo4j for the entity
        # Add to existing_entities_map
```

## Entity Instance ID Field

### Frontend Access (Step 1 Data)

The `entity_instance_id` is **present and accessible** in the proposal data returned from Step 1:

```typescript
type ArchitectProposal = {
  id: string;
  proposal_type: "new_instance" | "update_instance";
  entity_instance_id: string | null;  // ✓ Available here
  alias: string | null;
  // ... other fields
};
```

For **UPDATE_INSTANCE** proposals:
- `entity_instance_id` contains the UUID of the entity to update
- `alias` provides a human-readable name (if available)
- Both are returned in the Step 1 API response

### Backend Processing (Step 2)

The backend now:
1. Receives `entity_instance_id` in validated proposals
2. Loads the entity from anywhere in the graph (not just current instance)
3. Applies updates using LLM-extracted properties and relationships

## Documentation

Created `docs/architect_entity_ids.md` with:
- Detailed explanation of the entity_instance_id field
- How it flows from Step 1 to Step 2
- Frontend access patterns
- Technical implementation details

## Testing

Added test case in `backend/tests/test_entity_generator.py`:
- `test_entity_generator_updates_existing_entity`
- Verifies that entities can be updated even when not in the current instance

## Verification

- ✓ Code formatted with black
- ✓ Code review completed (2 issues addressed)
- ✓ Security scan completed (0 alerts)
- ✓ Logic verified with simulation
- ✓ Documentation added

## Impact

This fix ensures that:
1. **Cross-instance updates work**: Proposals can reference entities from any instance
2. **No data loss**: All valid entity_instance_ids from step 1 are processed
3. **Better logging**: Clear messages when loading entities from the graph
4. **User clarity**: Documentation explains what entity_instance_id is and where it comes from
