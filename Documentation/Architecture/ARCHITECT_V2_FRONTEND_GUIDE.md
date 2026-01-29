# Architect V2 Frontend Integration Guide

## Overview

The Architect V2 pipeline provides a more efficient and scalable entity extraction process. This document describes the JSON format that the frontend should expect from the API.

## API Endpoint

```
GET /api/jobs/architect/runs/{run_id}
```

## Response Structure

The response includes proposals with a new metadata structure that provides richer information:

### Basic Response Format

```json
{
  "id": "run-abc-123",
  "agent_id": "agent-xyz-789",
  "status": "completed",
  "input_chunk_count": 3,
  "proposals": [
    {
      "id": "prop-001",
      "proposal_type": "update_instance",
      "status": "pending",
      "entity_instance_id": "char_001",
      "alias": "Jessie Williams",
      "confidence": 0.89,
      "justification": "Main actor in the scene | Referenced in dialogue",
      "metadata": {
        "resolved_status": "existing",
        "mention_count": 2,
        "chunk_indices": [0, 1],
        "ontology_name": "Character"
      }
    },
    {
      "id": "prop-002",
      "proposal_type": "new_instance",
      "status": "pending",
      "entity_instance_id": null,
      "alias": "Baron Jackie",
      "confidence": 0.74,
      "justification": "Named individual that convenes the meeting",
      "metadata": {
        "resolved_status": "new",
        "mention_count": 1,
        "chunk_indices": [2],
        "ontology_name": "Character"
      }
    }
  ]
}
```

## New Fields in Metadata

### `resolved_status`
- Type: `string`
- Values: `"existing"` or `"new"`
- Description: Indicates whether the entity was matched to an existing node in the knowledge graph
- **Usage**: 
  - `"existing"`: Entity matched an existing node (show update UI)
  - `"new"`: Entity is new (show create UI)

### `mention_count`
- Type: `integer`
- Description: Number of times the entity was mentioned across all text chunks
- **Usage**: Can be used to sort or filter entities by importance

### `chunk_indices`
- Type: `array of integers`
- Description: List of chunk indices where the entity was mentioned
- **Usage**: Can be used to show context or navigate to relevant text sections

### `ontology_name`
- Type: `string`
- Description: The name of the ontology type (e.g., "Character", "Location", "Organization")
- **Usage**: Display the entity type in a human-readable format

## Proposal Types

### `update_instance`
- The entity matched an existing node
- `entity_instance_id` will contain the node ID
- `metadata.resolved_status` will be `"existing"`

### `new_instance`
- The entity is new and doesn't match any existing node
- `entity_instance_id` will be `null`
- `metadata.resolved_status` will be `"new"`

## Frontend Display Recommendations

### Entity List View

Sort entities by:
1. Confidence (descending)
2. Mention count (descending)

Display badges:
- "New" badge for `resolved_status === "new"`
- "Update" badge for `resolved_status === "existing"`
- Mention count badge (e.g., "Mentioned 5 times")

### Entity Detail View

Show:
- Entity name (from `alias`)
- Entity type (from `metadata.ontology_name`)
- Confidence score (with visual indicator)
- Status (New/Existing)
- Justification text
- For existing entities: link to the existing node
- Mention count and chunk references

### Filtering Options

Allow filtering by:
- `resolved_status` (New/Existing)
- `ontology_name` (Character/Location/etc.)
- Confidence threshold
- Minimum mention count

## Example Frontend Code

### TypeScript Interface

```typescript
interface ArchitectProposal {
  id: string;
  proposal_type: 'new_instance' | 'update_instance';
  status: 'pending' | 'approved' | 'rejected';
  entity_instance_id: string | null;
  alias: string;
  confidence: number;
  justification: string;
  metadata: {
    resolved_status: 'new' | 'existing';
    mention_count: number;
    chunk_indices: number[];
    ontology_name: string;
  };
}

interface ArchitectRun {
  id: string;
  agent_id: string;
  status: string;
  input_chunk_count: number;
  proposals: ArchitectProposal[];
}
```

### React Component Example

```tsx
import React from 'react';

interface EntityCardProps {
  proposal: ArchitectProposal;
}

const EntityCard: React.FC<EntityCardProps> = ({ proposal }) => {
  const isNew = proposal.metadata.resolved_status === 'new';
  
  return (
    <div className="entity-card">
      <div className="entity-header">
        <h3>{proposal.alias}</h3>
        <span className={`badge ${isNew ? 'badge-new' : 'badge-existing'}`}>
          {isNew ? 'New' : 'Update'}
        </span>
      </div>
      
      <div className="entity-meta">
        <span className="entity-type">{proposal.metadata.ontology_name}</span>
        <span className="entity-confidence">
          Confidence: {(proposal.confidence * 100).toFixed(0)}%
        </span>
        <span className="entity-mentions">
          Mentioned {proposal.metadata.mention_count} time(s)
        </span>
      </div>
      
      <p className="entity-justification">{proposal.justification}</p>
      
      {!isNew && proposal.entity_instance_id && (
        <a href={`/entities/${proposal.entity_instance_id}`}>
          View existing entity →
        </a>
      )}
    </div>
  );
};
```

## Migration Notes

### Breaking Changes
None - the V2 pipeline is backward compatible with the existing API structure.

### New Fields
All new fields are in the `metadata` object, so existing code won't break.

### Recommended Updates

1. **Use `resolved_status`**: This is more reliable than checking `entity_instance_id` for determining if an entity is new or existing

2. **Show mention count**: This helps users understand entity importance

3. **Display chunk references**: Allow users to see context by linking to specific text sections

4. **Filter by status**: Let users focus on new entities or updates separately

## Testing

You can test the new format using the example script:

```bash
cd backend
python examples/architect_v2_example.py
```

This will show you the exact JSON output format.

## Support

For questions or issues, please refer to:
- `ARCHITECT_V2_PIPELINE.md` - Detailed pipeline documentation
- `examples/architect_v2_example.py` - Working code example
- `tests/test_architect_v2_pipeline.py` - Test cases
