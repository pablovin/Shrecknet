# Architect Proposal Validation Integration - Summary

## Overview

This implementation ensures that both steps of the Architect workflow are properly connected through comprehensive proposal validation and correction capabilities. The client can now fully control how entity proposals are processed, including converting between proposal types and changing target entities.

## What Was Implemented

### 1. New Database Fields

Added to `architect_proposals` table:
- `corrected_proposal_type` - Allows converting NEW_INSTANCE ↔ UPDATE_INSTANCE
- `corrected_entity_instance_id` - Allows changing which entity to update

These fields complement the existing correction fields:
- `corrected_alias` - Fix entity name/alias
- `corrected_entity_definition_id` - Change entity type
- `merged_into_proposal_id` - Merge duplicate proposals

### 2. Client Capabilities

The client can now make these decisions on proposals:

#### A. Approve/Reject/Merge (Already Existed)
- **Approve**: Process the proposal
- **Reject**: Skip the proposal  
- **Merge**: Combine duplicate proposals

#### B. Convert Proposal Types (NEW)
- **NEW → UPDATE**: When client realizes a "new" entity already exists
  - Set `corrected_proposal_type: "update_instance"`
  - Provide `corrected_entity_instance_id` with existing entity ID
  
- **UPDATE → NEW**: When client realizes an "update" is actually a new entity
  - Set `corrected_proposal_type: "new_instance"`
  - Entity instance ID becomes None automatically

#### C. Change Update Target (NEW)
- For UPDATE_INSTANCE proposals, change which entity gets updated
- Use `corrected_entity_instance_id` to specify different target

#### D. Fix Entity Type (Already Existed)
- Change the entity definition (e.g., "Person" → "Deity")
- Use `corrected_entity_definition_id`

#### E. Fix Alias (Already Existed)
- Correct typos or formatting in entity names
- Use `corrected_alias`

### 3. Server-Side Integration

The entity generation task (`app/tasks/architect_generation.py`) now:

1. **Accepts corrected proposal types**: Reads `corrected_proposal_type` from client
2. **Handles entity instance ID corrections**: Properly handles None values when converting to NEW_INSTANCE
3. **Routes proposals correctly**: Uses effective types to separate NEW vs UPDATE proposals
4. **Applies all corrections**: Entity type, alias, target entity all properly applied

Key logic improvements:
```python
# Use corrected type if provided
effective_proposal_type = (
    p.corrected_proposal_type 
    if p.corrected_proposal_type is not None 
    else p.proposal_type
)

# Handle None properly when converting UPDATE→NEW
if (
    p.corrected_entity_instance_id is not None 
    or p.corrected_proposal_type == ArchitectProposalType.NEW_INSTANCE
):
    effective_entity_instance_id = p.corrected_entity_instance_id
else:
    effective_entity_instance_id = p.entity_instance_id
```

### 4. Complete Documentation

Created comprehensive documentation:

- **ARCHITECT_API_EXAMPLES.md**: Full input/output examples for all scenarios
  - Step 1: Request analysis
  - Step 2: Submit validated proposals
  - 7+ client decision scenarios with examples
  - Complete validation rules
  - Error scenarios

- **Updated ARCHITECT_STEP2_API.md**: 
  - New fields documented
  - Client capabilities explained
  - Reference to comprehensive examples

- **Updated ARCHITECT_STEP2_IMPLEMENTATION.md**:
  - New database fields listed
  - New schemas documented
  - Client capabilities summarized

### 5. Comprehensive Tests

Created test suites:

- **test_architect_validation.py**: Tests for validation schemas
  - Basic proposal validation
  - All correction types
  - Serialization/deserialization
  - Enum values

- **test_architect_proposal_corrections.py**: Integration tests
  - Effective proposal type logic
  - Effective entity instance ID logic
  - Conversion scenarios (NEW↔UPDATE)
  - Change target scenarios
  - Separation logic for routing proposals

All tests pass successfully.

## Data Flow

### Step 1: Server → Client

Server sends proposals with these fields:
```json
{
  "id": "prop-123",
  "proposal_type": "new_instance",
  "status": "pending",
  "entity_definition_id": 5,
  "entity_instance_id": null,
  "alias": "John Smith",
  "chunks": ["..."],
  "corrected_alias": null,
  "corrected_entity_definition_id": null,
  "corrected_proposal_type": null,
  "corrected_entity_instance_id": null,
  "merged_into_proposal_id": null,
  "generated_entity_instance_id": null
}
```

### Step 2: Client → Server

Client sends validated proposals with corrections:
```json
{
  "proposal_id": "prop-123",
  "status": "approved",
  "corrected_alias": "Jonathan Smith",
  "corrected_entity_definition_id": 8,
  "corrected_proposal_type": "update_instance",
  "corrected_entity_instance_id": "entity-existing-456",
  "merged_into_proposal_id": null
}
```

### Step 3: Server Processing

Server applies corrections:
1. Uses `corrected_proposal_type` if provided, else original
2. Uses `corrected_entity_instance_id` if provided or if converting to NEW
3. Uses `corrected_alias` and `corrected_entity_definition_id` in entity creation/update
4. Merges chunks from merged proposals
5. Creates/updates entities based on effective values

### Step 4: Server → Client (Results)

Server returns updated proposals with generated entity IDs:
```json
{
  "id": "prop-123",
  "proposal_type": "new_instance",
  "status": "approved",
  "corrected_proposal_type": "update_instance",
  "corrected_entity_instance_id": "entity-existing-456",
  "generated_entity_instance_id": "entity-existing-456"
}
```

## Example Use Cases

### Use Case 1: Convert NEW to UPDATE
**Scenario**: Architect suggests creating "Bob Johnson" but user knows he already exists as entity-bob-123

**Client sends**:
```json
{
  "proposal_id": "prop-new-bob",
  "status": "approved",
  "corrected_proposal_type": "update_instance",
  "corrected_entity_instance_id": "entity-bob-123"
}
```

**Result**: Instead of creating new Bob, updates existing entity-bob-123 with new information

### Use Case 2: Convert UPDATE to NEW
**Scenario**: Architect suggests updating "Alice Smith" (entity-alice-123) but the text is actually about her daughter "Alice Smith Jr."

**Client sends**:
```json
{
  "proposal_id": "prop-update-alice",
  "status": "approved",
  "corrected_alias": "Alice Smith Jr.",
  "corrected_proposal_type": "new_instance"
}
```

**Result**: Creates new entity "Alice Smith Jr." instead of updating the mother's record

### Use Case 3: Change Update Target
**Scenario**: Architect suggests updating entity-location-A but the information actually belongs to entity-location-B

**Client sends**:
```json
{
  "proposal_id": "prop-update-location",
  "status": "approved",
  "corrected_entity_instance_id": "entity-location-B"
}
```

**Result**: entity-location-B gets updated instead of entity-location-A

### Use Case 4: Multiple Corrections
**Scenario**: Architect suggests creating "Jon Doe" as a "Person" but it should be "John Doe" and he's actually a "Deity"

**Client sends**:
```json
{
  "proposal_id": "prop-jon",
  "status": "approved",
  "corrected_alias": "John Doe",
  "corrected_entity_definition_id": 8
}
```

**Result**: Creates entity "John Doe" as entity type 8 (Deity) instead of original type

## Key Benefits

1. **Full Client Control**: Client has complete authority over entity creation/updates
2. **Flexibility**: Can convert between NEW and UPDATE based on knowledge of existing entities
3. **Accuracy**: Can fix typos, change types, and correct target entities
4. **Efficiency**: Merge duplicates and avoid creating redundant entities
5. **Transparency**: All corrections tracked and visible in proposals
6. **Backward Compatible**: All correction fields are optional

## Migration

The migration (`app/db/migrations.py`) automatically adds the new columns when the database initializes:
- `corrected_proposal_type` (VARCHAR)
- `corrected_entity_instance_id` (VARCHAR)

Existing deployments will seamlessly upgrade on next startup.

## Files Changed

### Core Implementation
- `app/models/architect.py` - Added database fields
- `app/schemas/architect.py` - Added request/response fields
- `app/repositories/architect_repository.py` - Handle new fields in updates
- `app/tasks/architect_generation.py` - Apply corrections during entity generation
- `app/db/migrations.py` - Add migration for new columns

### Documentation
- `backend_2/ARCHITECT_API_EXAMPLES.md` - Comprehensive examples (NEW)
- `backend_2/ARCHITECT_STEP2_API.md` - Updated with new capabilities
- `ARCHITECT_STEP2_IMPLEMENTATION.md` - Updated summary

### Tests
- `backend_2/tests/test_architect_validation.py` - Schema validation tests (NEW)
- `backend_2/tests/test_architect_proposal_corrections.py` - Integration tests (NEW)

## Testing

All functionality verified through:
1. ✅ Schema validation tests (15 test cases)
2. ✅ Proposal correction logic tests (10 test cases)
3. ✅ Manual validation of core functionality
4. ✅ Documentation with complete examples

## Next Steps for Frontend

The frontend should implement:

1. **Proposal Display**: Show all proposals from step 1 with their suggestions
2. **Approval UI**: Allow approve/reject/merge for each proposal
3. **Correction Forms**:
   - Text input for alias corrections
   - Dropdown for entity type changes
   - Toggle/select for proposal type conversion
   - Entity search/select for target entity changes
4. **Validation**: Ensure NEW→UPDATE includes entity instance ID
5. **Submission**: POST validated proposals to generate endpoint
6. **Progress Tracking**: Poll background job for completion
7. **Results Display**: Show generated entity IDs and link to entities

## Conclusion

The Architect workflow now has complete bidirectional communication:
- **Step 1**: Server analyzes and proposes entities to client
- **Step 2**: Client validates, corrects, and returns proposals to server
- **Server**: Processes with full awareness of all client decisions

All entity creation/update decisions can be modified by the client, ensuring accuracy and preventing duplicates or incorrect categorizations.
