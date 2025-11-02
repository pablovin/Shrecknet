# Architect API - Complete Input/Output Examples

This document provides comprehensive examples of all data flows between client and server for the Architect job workflow.

## Overview

The Architect workflow has two steps:
1. **Step 1 (Analysis)**: Server analyzes text and sends entity proposals to the client
2. **Step 2 (Generation)**: Client sends validated proposals back, server generates/updates entities

## Step 1: Request Analysis

### Endpoint
```
POST /api/jobs/architect/{agent_id}/analyze
```

### Input Example
```json
{
  "ontology_instance_id": "game-world-001",
  "ontology_id": 42,
  "max_chunks": 50,
  "chunk_size": 1200
}
```

### Response Example
```json
{
  "id": "run-abc-123",
  "agent_id": "agent-xyz-789",
  "background_job_id": 1001,
  "ontology_id": 42,
  "ontology_instance_id": "game-world-001",
  "status": "pending",
  "input_chunk_count": null,
  "settings": {
    "requested_by": "user-123",
    "max_chunks": 50,
    "chunk_size": 1200
  },
  "created_at": "2025-11-02T10:00:00Z",
  "updated_at": "2025-11-02T10:00:00Z",
  "proposals": []
}
```

## Step 1: Poll for Completion

### Endpoint
```
GET /api/jobs/architect/runs/{run_id}
```

### Response Example (Completed)
```json
{
  "id": "run-abc-123",
  "agent_id": "agent-xyz-789",
  "background_job_id": 1001,
  "ontology_id": 42,
  "ontology_instance_id": "game-world-001",
  "status": "completed",
  "input_chunk_count": 15,
  "settings": {
    "requested_by": "user-123",
    "max_chunks": 50,
    "chunk_size": 1200
  },
  "created_at": "2025-11-02T10:00:00Z",
  "updated_at": "2025-11-02T10:05:30Z",
  "proposals": [
    {
      "id": "prop-001",
      "proposal_type": "new_instance",
      "status": "pending",
      "entity_definition_id": 5,
      "entity_instance_id": null,
      "alias": "John Smith",
      "confidence": 0.85,
      "justification": "Character mentioned in chapter 3 with significant dialogue",
      "evidence": [
        {
          "chunk_index": 2,
          "text": "John Smith arrived at the castle gates..."
        }
      ],
      "metadata": {
        "source_chunks": [2, 3, 5]
      },
      "chunks": [
        "John Smith arrived at the castle gates...",
        "John spoke with the guards...",
        "Later, John met with the king..."
      ],
      "merged_into_proposal_id": null,
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "generated_entity_instance_id": null,
      "created_at": "2025-11-02T10:05:00Z",
      "updated_at": "2025-11-02T10:05:00Z"
    },
    {
      "id": "prop-002",
      "proposal_type": "update_instance",
      "status": "pending",
      "entity_definition_id": 5,
      "entity_instance_id": "entity-alice-456",
      "alias": "Alice",
      "confidence": 0.92,
      "justification": "Existing character with new information about her background",
      "evidence": [
        {
          "chunk_index": 7,
          "text": "Alice revealed she was from the northern kingdom..."
        }
      ],
      "metadata": {
        "source_chunks": [7]
      },
      "chunks": [
        "Alice revealed she was from the northern kingdom..."
      ],
      "merged_into_proposal_id": null,
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "generated_entity_instance_id": null,
      "created_at": "2025-11-02T10:05:10Z",
      "updated_at": "2025-11-02T10:05:10Z"
    },
    {
      "id": "prop-003",
      "proposal_type": "new_instance",
      "status": "pending",
      "entity_definition_id": 10,
      "entity_instance_id": null,
      "alias": "Shadowmere Castle",
      "confidence": 0.78,
      "justification": "Location frequently mentioned in the narrative",
      "evidence": [
        {
          "chunk_index": 1,
          "text": "The journey to Shadowmere Castle took three days..."
        }
      ],
      "metadata": {
        "source_chunks": [1, 4, 8]
      },
      "chunks": [
        "The journey to Shadowmere Castle took three days...",
        "Shadowmere Castle stood on a cliff...",
        "Inside Shadowmere Castle, the halls were dark..."
      ],
      "merged_into_proposal_id": null,
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "generated_entity_instance_id": null,
      "created_at": "2025-11-02T10:05:15Z",
      "updated_at": "2025-11-02T10:05:15Z"
    },
    {
      "id": "prop-004",
      "proposal_type": "new_instance",
      "status": "pending",
      "entity_definition_id": 5,
      "entity_instance_id": null,
      "alias": "Jon Smith",
      "confidence": 0.70,
      "justification": "Possible duplicate of John Smith (spelling variation)",
      "evidence": [
        {
          "chunk_index": 10,
          "text": "Jon Smith greeted the guards..."
        }
      ],
      "metadata": {
        "source_chunks": [10]
      },
      "chunks": [
        "Jon Smith greeted the guards..."
      ],
      "merged_into_proposal_id": null,
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "generated_entity_instance_id": null,
      "created_at": "2025-11-02T10:05:20Z",
      "updated_at": "2025-11-02T10:05:20Z"
    }
  ]
}
```

## Step 2: Submit Validated Proposals

### Endpoint
```
POST /api/jobs/architect/runs/{run_id}/generate
```

### Input Example - All Validation Scenarios

```json
{
  "run_id": "run-abc-123",
  "author_type": "user",
  "author_id": "user-123",
  "validated_proposals": [
    {
      "proposal_id": "prop-001",
      "status": "approved",
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "merged_into_proposal_id": null
    },
    {
      "proposal_id": "prop-002",
      "status": "approved",
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": "entity-alice-789",
      "merged_into_proposal_id": null
    },
    {
      "proposal_id": "prop-003",
      "status": "approved",
      "corrected_alias": "Shadowmere Fortress",
      "corrected_entity_definition_id": 11,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "merged_into_proposal_id": null
    },
    {
      "proposal_id": "prop-004",
      "status": "merged",
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "merged_into_proposal_id": "prop-001"
    }
  ]
}
```

**Explanation of Each Proposal:**

1. **prop-001**: Approved as-is, no changes
2. **prop-002**: Approved, but client changed which entity instance to update (from entity-alice-456 to entity-alice-789)
3. **prop-003**: Approved with corrections - alias changed to "Shadowmere Fortress" and entity type changed from 10 to 11
4. **prop-004**: Merged into prop-001 (it was a duplicate/typo of John Smith)

### Response Example
```json
{
  "status": "accepted",
  "task_id": "celery-task-abc-xyz-123",
  "run_id": "run-abc-123",
  "message": "Entity generation task started"
}
```

## Client Decision Scenarios

### Scenario 1: Convert NEW_INSTANCE to UPDATE_INSTANCE

**Use Case**: Client realizes a proposed "new" entity actually already exists in the system.

**Input:**
```json
{
  "run_id": "run-abc-123",
  "author_type": "user",
  "author_id": "user-123",
  "validated_proposals": [
    {
      "proposal_id": "prop-005",
      "status": "approved",
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": "update_instance",
      "corrected_entity_instance_id": "entity-existing-999",
      "merged_into_proposal_id": null
    }
  ]
}
```

**Explanation**: 
- Original proposal was `new_instance` for "Bob Johnson"
- Client found that Bob Johnson already exists as entity-existing-999
- Changed proposal type to `update_instance` and specified the existing entity ID

### Scenario 2: Convert UPDATE_INSTANCE to NEW_INSTANCE

**Use Case**: Client realizes a proposed "update" to an entity is actually describing a different new entity.

**Input:**
```json
{
  "run_id": "run-abc-123",
  "author_type": "user",
  "author_id": "user-123",
  "validated_proposals": [
    {
      "proposal_id": "prop-006",
      "status": "approved",
      "corrected_alias": "Bob Johnson Jr.",
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": "new_instance",
      "corrected_entity_instance_id": null,
      "merged_into_proposal_id": null
    }
  ]
}
```

**Explanation**:
- Original proposal was `update_instance` for entity-bob-senior-123
- Client realized the text is about Bob's son, not Bob himself
- Changed proposal type to `new_instance` and corrected the alias to "Bob Johnson Jr."

### Scenario 3: Change Target Entity for Update

**Use Case**: Client corrects which entity instance should be updated.

**Input:**
```json
{
  "run_id": "run-abc-123",
  "author_type": "user",
  "author_id": "user-123",
  "validated_proposals": [
    {
      "proposal_id": "prop-007",
      "status": "approved",
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": "entity-correct-target-555",
      "merged_into_proposal_id": null
    }
  ]
}
```

**Explanation**:
- Original proposal suggested updating entity-wrong-target-444
- Client identified that the information actually belongs to entity-correct-target-555
- Corrected the target entity instance ID

### Scenario 4: Fix Entity Type

**Use Case**: Client corrects the entity type/definition.

**Input:**
```json
{
  "run_id": "run-abc-123",
  "author_type": "user",
  "author_id": "user-123",
  "validated_proposals": [
    {
      "proposal_id": "prop-008",
      "status": "approved",
      "corrected_alias": null,
      "corrected_entity_definition_id": 8,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "merged_into_proposal_id": null
    }
  ]
}
```

**Explanation**:
- Original proposal had entity_definition_id: 5 (e.g., "Person")
- Client realized it should be entity_definition_id: 8 (e.g., "Deity")
- Corrected the entity type

### Scenario 5: Fix Alias (Typo)

**Use Case**: Client fixes a typo or formatting in the entity alias.

**Input:**
```json
{
  "run_id": "run-abc-123",
  "author_type": "user",
  "author_id": "user-123",
  "validated_proposals": [
    {
      "proposal_id": "prop-009",
      "status": "approved",
      "corrected_alias": "Catherine the Great",
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "merged_into_proposal_id": null
    }
  ]
}
```

**Explanation**:
- Original proposal had alias: "catherine the great" (lowercase)
- Client corrected to proper case: "Catherine the Great"

### Scenario 6: Reject Proposal

**Use Case**: Client doesn't want this entity created/updated.

**Input:**
```json
{
  "run_id": "run-abc-123",
  "author_type": "user",
  "author_id": "user-123",
  "validated_proposals": [
    {
      "proposal_id": "prop-010",
      "status": "rejected",
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "merged_into_proposal_id": null
    }
  ]
}
```

**Explanation**:
- Proposal is marked as rejected
- Will not be processed by the generation step

### Scenario 7: Merge Duplicates

**Use Case**: Client identifies duplicate proposals and merges them.

**Input:**
```json
{
  "run_id": "run-abc-123",
  "author_type": "user",
  "author_id": "user-123",
  "validated_proposals": [
    {
      "proposal_id": "prop-011",
      "status": "approved",
      "corrected_alias": "Elizabeth Smith",
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "merged_into_proposal_id": null
    },
    {
      "proposal_id": "prop-012",
      "status": "merged",
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "merged_into_proposal_id": "prop-011"
    },
    {
      "proposal_id": "prop-013",
      "status": "merged",
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "merged_into_proposal_id": "prop-011"
    }
  ]
}
```

**Explanation**:
- prop-011, prop-012, and prop-013 all refer to "Elizabeth Smith" (with typos like "Liz Smith", "Beth Smith")
- Client approves prop-011 with corrected alias
- prop-012 and prop-013 are merged into prop-011
- Their text chunks will be combined during generation

## Step 3: Track Generation Progress

### Endpoint
```
GET /api/jobs/background/{job_id}
```

### Response Example (In Progress)
```json
{
  "id": 1002,
  "author_type": "user",
  "author_id": "user-123",
  "job_type": "architect_generation",
  "status": "running",
  "description": "Architect entity generation for run run-abc-123",
  "progress": 0.45,
  "details": {
    "run_id": "run-abc-123",
    "proposal_count": 4,
    "status": "Generating 2 new entities"
  },
  "result": null,
  "error_message": null,
  "created_at": "2025-11-02T10:10:00Z",
  "updated_at": "2025-11-02T10:10:30Z",
  "started_at": "2025-11-02T10:10:05Z",
  "completed_at": null
}
```

### Response Example (Completed)
```json
{
  "id": 1002,
  "author_type": "user",
  "author_id": "user-123",
  "job_type": "architect_generation",
  "status": "done",
  "description": "Architect entity generation for run run-abc-123",
  "progress": 1.0,
  "details": {
    "run_id": "run-abc-123",
    "proposal_count": 4,
    "status": "Entity generation completed"
  },
  "result": {
    "run_id": "run-abc-123",
    "created_entities": 2,
    "updated_entities": 1,
    "status": "completed"
  },
  "error_message": null,
  "created_at": "2025-11-02T10:10:00Z",
  "updated_at": "2025-11-02T10:12:45Z",
  "started_at": "2025-11-02T10:10:05Z",
  "completed_at": "2025-11-02T10:12:45Z"
}
```

## Step 4: Retrieve Generated Entities

### Endpoint
```
GET /api/jobs/architect/runs/{run_id}
```

### Response Example (After Generation)
```json
{
  "id": "run-abc-123",
  "agent_id": "agent-xyz-789",
  "background_job_id": 1001,
  "ontology_id": 42,
  "ontology_instance_id": "game-world-001",
  "status": "completed",
  "input_chunk_count": 15,
  "settings": {
    "requested_by": "user-123",
    "max_chunks": 50,
    "chunk_size": 1200
  },
  "created_at": "2025-11-02T10:00:00Z",
  "updated_at": "2025-11-02T10:12:45Z",
  "proposals": [
    {
      "id": "prop-001",
      "proposal_type": "new_instance",
      "status": "approved",
      "entity_definition_id": 5,
      "entity_instance_id": null,
      "alias": "John Smith",
      "confidence": 0.85,
      "justification": "Character mentioned in chapter 3 with significant dialogue",
      "evidence": [{"chunk_index": 2, "text": "John Smith arrived..."}],
      "metadata": {"source_chunks": [2, 3, 5]},
      "chunks": ["John Smith arrived...", "John spoke...", "Later, John met..."],
      "merged_into_proposal_id": null,
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "generated_entity_instance_id": "entity-john-smith-new-123",
      "created_at": "2025-11-02T10:05:00Z",
      "updated_at": "2025-11-02T10:12:30Z"
    },
    {
      "id": "prop-002",
      "proposal_type": "update_instance",
      "status": "approved",
      "entity_definition_id": 5,
      "entity_instance_id": "entity-alice-456",
      "alias": "Alice",
      "confidence": 0.92,
      "justification": "Existing character with new information",
      "evidence": [{"chunk_index": 7, "text": "Alice revealed..."}],
      "metadata": {"source_chunks": [7]},
      "chunks": ["Alice revealed she was from..."],
      "merged_into_proposal_id": null,
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": "entity-alice-789",
      "generated_entity_instance_id": "entity-alice-789",
      "created_at": "2025-11-02T10:05:10Z",
      "updated_at": "2025-11-02T10:12:35Z"
    },
    {
      "id": "prop-003",
      "proposal_type": "new_instance",
      "status": "approved",
      "entity_definition_id": 10,
      "entity_instance_id": null,
      "alias": "Shadowmere Castle",
      "confidence": 0.78,
      "justification": "Location frequently mentioned",
      "evidence": [{"chunk_index": 1, "text": "The journey to..."}],
      "metadata": {"source_chunks": [1, 4, 8]},
      "chunks": ["The journey to...", "Shadowmere Castle stood...", "Inside..."],
      "merged_into_proposal_id": null,
      "corrected_alias": "Shadowmere Fortress",
      "corrected_entity_definition_id": 11,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "generated_entity_instance_id": "entity-shadowmere-fortress-456",
      "created_at": "2025-11-02T10:05:15Z",
      "updated_at": "2025-11-02T10:12:40Z"
    },
    {
      "id": "prop-004",
      "proposal_type": "new_instance",
      "status": "merged",
      "entity_definition_id": 5,
      "entity_instance_id": null,
      "alias": "Jon Smith",
      "confidence": 0.70,
      "justification": "Possible duplicate",
      "evidence": [{"chunk_index": 10, "text": "Jon Smith greeted..."}],
      "metadata": {"source_chunks": [10]},
      "chunks": ["Jon Smith greeted..."],
      "merged_into_proposal_id": "prop-001",
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "generated_entity_instance_id": null,
      "created_at": "2025-11-02T10:05:20Z",
      "updated_at": "2025-11-02T10:12:25Z"
    }
  ]
}
```

**Key Points:**
- `prop-001`: Created new entity with ID "entity-john-smith-new-123"
- `prop-002`: Updated entity "entity-alice-789" (note: client changed from original entity-alice-456)
- `prop-003`: Created new entity with corrected alias and type
- `prop-004`: Merged into prop-001, no entity created (chunks combined with prop-001)

## Complete Validation Rules

### Status Field
- `approved`: Process this proposal
- `rejected`: Skip this proposal
- `merged`: Don't process this proposal directly; its data is merged into the target proposal

### Corrected Fields

| Field | When Used | Effect |
|-------|-----------|--------|
| `corrected_alias` | Client fixes typo or improves formatting | Uses corrected alias instead of original |
| `corrected_entity_definition_id` | Client changes entity type | Uses corrected type instead of original |
| `corrected_proposal_type` | Client converts between NEW/UPDATE | Changes whether entity is created or updated |
| `corrected_entity_instance_id` | Client changes update target | Updates different entity than originally proposed |
| `merged_into_proposal_id` | Client identifies duplicate | Combines chunks; only processes target proposal |

### Important Notes

1. **Proposal Type Conversion**:
   - `NEW_INSTANCE` → `UPDATE_INSTANCE`: Must provide `corrected_entity_instance_id`
   - `UPDATE_INSTANCE` → `NEW_INSTANCE`: Set `corrected_entity_instance_id` to null

2. **Entity Instance ID**:
   - For `UPDATE_INSTANCE` proposals, can use `corrected_entity_instance_id` to change target
   - For `NEW_INSTANCE` proposals, `corrected_entity_instance_id` should be null

3. **Merging**:
   - Merged proposals inherit text chunks
   - Only the target proposal creates/updates an entity
   - Merged proposals don't get `generated_entity_instance_id`

4. **All corrections are optional**:
   - Only provide fields that need to be changed
   - Server uses original values for any null corrected fields

## Error Scenarios

### Invalid Proposal Type Conversion
```json
{
  "run_id": "run-abc-123",
  "author_type": "user",
  "author_id": "user-123",
  "validated_proposals": [
    {
      "proposal_id": "prop-005",
      "status": "approved",
      "corrected_proposal_type": "update_instance",
      "corrected_entity_instance_id": null
    }
  ]
}
```

**Error**: Converting to UPDATE_INSTANCE requires `corrected_entity_instance_id`.

### Missing Run
```json
{
  "run_id": "non-existent-run",
  "author_type": "user",
  "author_id": "user-123",
  "validated_proposals": [...]
}
```

**Response**:
```json
{
  "detail": "Architect run not found"
}
```
**Status**: 404

### Invalid Merge Target
```json
{
  "run_id": "run-abc-123",
  "author_type": "user",
  "author_id": "user-123",
  "validated_proposals": [
    {
      "proposal_id": "prop-005",
      "status": "merged",
      "merged_into_proposal_id": "non-existent-proposal"
    }
  ]
}
```

**Error**: Merge target proposal doesn't exist in the same run.
