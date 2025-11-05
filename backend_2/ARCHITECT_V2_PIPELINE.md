# Architect V2 Pipeline - Efficient and Scalable Entity Extraction

This document describes the redesigned Architect pipeline (V2) that improves efficiency and scalability.

## Overview

The new pipeline consists of 4 steps:

0. **Preload**: Gather node catalogue and ontology definitions (one-time per batch)
1. **Chunk-level Entity Extraction**: Extract entities from each chunk with slim JSON
2. **Global Deduplication**: Deduplicate entities across all chunks programmatically
3. **Reconciliation**: Use LLM to match proposed entities with existing ones
4. **Map Back to JSON**: Create final output with resolved status for frontend

## Step-by-Step Examples

### Step 0: Preload (One-time per ontology/document batch)

**Input sources you already have:**

Existing node catalogue (from DB):
```json
[
  {
    "node_id": "char_001",
    "alias": "Jessie Williams",
    "ontology": "Character"
  },
  {
    "node_id": "org_101",
    "alias": "Camarilla",
    "ontology": "Organization"
  },
  {
    "node_id": "ent_777",
    "alias": "Mithras",
    "ontology": "NPC"
  }
]
```

Ontology definitions (your "guide"):
```json
{
  "ontology": [
    { "name": "Character", "description": "People, mortals, vampires, mages..." },
    { "name": "Location", "description": "Cities, places, domains..." },
    { "name": "Organization", "description": "Coteries, clans, sects..." },
    { "name": "Deity", "description": "God-like entities, mythic beings..." },
    { "name": "NPC", "description": "Non-player characters..." }
  ]
}
```

### Step 1: Chunk-Level Entity Proposals

For each text chunk, we do a very constrained extraction.

**Goal of this step:**
- Detect mentions
- Decide the best ontology type from your ontology
- Give a confidence
- Give a 1-line justification
- No duplicates inside the same chunk
- No parenthesis variants or "Jessie (old)" → normalize

**Example Input (LLM Prompt):**
```
You are extracting entities from a single text chunk...

Ontology Definitions:
- Character: People, mortals, vampires, mages...
- Location: Cities, places, domains...
- Organization: Coteries, clans, sects...

Text Chunk:
"""
Jessie Williams arrived at the Camarilla headquarters. She met with the leadership
to discuss the upcoming conflict. Mithras, the ancient one, was mentioned as a
potential ally.
"""

Rules:
- Do NOT create variants like "Jessie (old)" and "Jessie"
- Keep only the most complete name
- Output SLIM JSON
...
```

**Example Output (LLM Response):**
```json
{
  "entities": [
    {
      "name": "Jessie Williams",
      "ontology": "Character",
      "confidence": 0.92,
      "why": "Main actor in the scene, referenced by others."
    },
    {
      "name": "Camarilla",
      "ontology": "Organization",
      "confidence": 0.88,
      "why": "Power group mentioned as location and antagonist."
    },
    {
      "name": "Mithras",
      "ontology": "NPC",
      "confidence": 0.75,
      "why": "Ancient entity mentioned as potential ally."
    }
  ]
}
```

**All Chunks Output (Internal Format):**
```json
[
  {
    "chunk_id": "chunk_001",
    "chunk_index": 0,
    "entities": [
      {
        "name": "Jessie Williams",
        "ontology": "Character",
        "confidence": 0.92,
        "why": "Main actor in the scene, referenced by others."
      },
      {
        "name": "Camarilla",
        "ontology": "Organization",
        "confidence": 0.88,
        "why": "Power group mentioned as location and antagonist."
      }
    ]
  },
  {
    "chunk_id": "chunk_002",
    "chunk_index": 1,
    "entities": [
      {
        "name": "Jessie",
        "ontology": "Character",
        "confidence": 0.85,
        "why": "Referenced in dialogue."
      },
      {
        "name": "Mithras",
        "ontology": "NPC",
        "confidence": 0.75,
        "why": "Ancient entity mentioned as potential ally."
      }
    ]
  },
  {
    "chunk_id": "chunk_003",
    "chunk_index": 2,
    "entities": [
      {
        "name": "Baron Jackie",
        "ontology": "Character",
        "confidence": 0.74,
        "why": "Named individual that convenes the meeting."
      }
    ]
  }
]
```

### Step 2: Global De-dup Across Chunks (Programmatic)

Now you have a list from all chunks. We flatten and run programmatic dedup:

**Deduplication Logic:**
- Lowercase + strip + remove parenthesis content for comparisons
  - "Mithras (god)" → "mithras"
  - "Jessie" → "jessie"
  - "Jessie Williams" → "jessie williams"
- If a shorter name and a longer name collide, prefer the longer one
- Keep the highest confidence version
- Keep the original canonical name (the longer one) for the final LLM pass

**Example Output (Internal Format):**
```json
{
  "proposed_entities": [
    {
      "name": "Jessie Williams",
      "ontology": "Character",
      "confidence": 0.89,
      "justifications": [
        "Main actor in the scene, referenced by others.",
        "Referenced in dialogue."
      ],
      "chunk_indices": [0, 1]
    },
    {
      "name": "Camarilla",
      "ontology": "Organization",
      "confidence": 0.88,
      "justifications": [
        "Power group mentioned as location and antagonist."
      ],
      "chunk_indices": [0]
    },
    {
      "name": "Mithras",
      "ontology": "NPC",
      "confidence": 0.75,
      "justifications": [
        "Ancient entity mentioned as potential ally."
      ],
      "chunk_indices": [1]
    },
    {
      "name": "Baron Jackie",
      "ontology": "Character",
      "confidence": 0.74,
      "justifications": [
        "Named individual that convenes the meeting."
      ],
      "chunk_indices": [2]
    }
  ]
}
```

### Step 3: Reconciliation With Existing Nodes (LLM)

This is where we ask the LLM to match proposed entities with existing ones.

**Example Input (LLM Prompt):**
```
You are reconciling extracted entities with an existing knowledge graph.

Proposed Entities (extracted from text):
[
  { "name": "Jessie Williams", "ontology": "Character" },
  { "name": "Camarilla", "ontology": "Organization" },
  { "name": "Mithras", "ontology": "NPC" },
  { "name": "Baron Jackie", "ontology": "Character" }
]

Existing Entities (from knowledge graph):
[
  { "node_id": "char_001", "alias": "Jessie Williams", "ontology": "Character" },
  { "node_id": "org_101", "alias": "Camarilla", "ontology": "Organization" },
  { "node_id": "ent_777", "alias": "Mithras", "ontology": "NPC" }
]

Your task:
- For each proposed entity, decide if it is the same as an existing entity
- Prefer matches even if the name is slightly different
- "Jessie" and "Jessie Williams" are the SAME
- "Mithras (god)" and "Mithras" are the SAME
- Output ONLY JSON with two arrays: existing and new
...
```

**Example Output (LLM Response):**
```json
{
  "existing": [
    {
      "proposed_name": "Jessie Williams",
      "matched_node_id": "char_001",
      "ontology": "Character"
    },
    {
      "proposed_name": "Camarilla",
      "matched_node_id": "org_101",
      "ontology": "Organization"
    },
    {
      "proposed_name": "Mithras",
      "matched_node_id": "ent_777",
      "ontology": "NPC"
    }
  ],
  "new": [
    {
      "name": "Baron Jackie",
      "ontology": "Character"
    }
  ]
}
```

### Step 4: Map Back to Original JSON (For Frontend)

Final payload that includes all the information needed by the frontend:

**Example Output (Final JSON to Frontend):**
```json
{
  "entities": [
    {
      "name": "Jessie Williams",
      "ontology": "Character",
      "confidence": 0.89,
      "why": "Main actor in the scene, referenced by others. | Referenced in dialogue.",
      "resolved_status": "existing",
      "resolved_node_id": "char_001",
      "mention_count": 2,
      "chunk_indices": [0, 1]
    },
    {
      "name": "Camarilla",
      "ontology": "Organization",
      "confidence": 0.88,
      "why": "Power group mentioned as location and antagonist.",
      "resolved_status": "existing",
      "resolved_node_id": "org_101",
      "mention_count": 1,
      "chunk_indices": [0]
    },
    {
      "name": "Mithras",
      "ontology": "NPC",
      "confidence": 0.75,
      "why": "Ancient entity mentioned as potential ally.",
      "resolved_status": "existing",
      "resolved_node_id": "ent_777",
      "mention_count": 1,
      "chunk_indices": [1]
    },
    {
      "name": "Baron Jackie",
      "ontology": "Character",
      "confidence": 0.74,
      "why": "Named individual that convenes the meeting.",
      "resolved_status": "new",
      "resolved_node_id": null,
      "mention_count": 1,
      "chunk_indices": [2]
    }
  ]
}
```

## Database Format

The proposals are stored in the database with the following structure:

```json
{
  "proposal_type": "new_instance",
  "entity_definition_id": null,
  "entity_instance_id": null,
  "alias": "Baron Jackie",
  "confidence": 0.74,
  "justification": "Named individual that convenes the meeting.",
  "proposal_metadata": {
    "resolved_status": "new",
    "mention_count": 1,
    "chunk_indices": [2],
    "ontology_name": "Character"
  },
  "chunks": []
}
```

For existing entities:

```json
{
  "proposal_type": "update_instance",
  "entity_definition_id": null,
  "entity_instance_id": "char_001",
  "alias": "Jessie Williams",
  "confidence": 0.89,
  "justification": "Main actor in the scene, referenced by others. | Referenced in dialogue.",
  "proposal_metadata": {
    "resolved_status": "existing",
    "mention_count": 2,
    "chunk_indices": [0, 1],
    "ontology_name": "Character"
  },
  "chunks": []
}
```

## API Response Format

When fetching from the API endpoint `/api/jobs/architect/runs/{run_id}`, the response includes:

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
      "justification": "Main actor in the scene, referenced by others. | Referenced in dialogue.",
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
      "justification": "Named individual that convenes the meeting.",
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

## Benefits of V2 Pipeline

1. **Efficiency**: Chunk-level extraction uses slim JSON, reducing token usage
2. **Scalability**: Deduplication is done programmatically, not via LLM
3. **Accuracy**: Reconciliation step ensures proper matching with existing entities
4. **Transparency**: Each step's output is clear and debuggable
5. **Flexibility**: Easy to adjust individual steps without affecting the whole pipeline

## Migration Notes

The V2 pipeline is backward compatible with the existing database schema. The main differences are:

- `proposal_metadata` now includes `resolved_status`, `mention_count`, and `chunk_indices`
- The reconciliation step is new and improves accuracy
- The pipeline is more modular and easier to test
