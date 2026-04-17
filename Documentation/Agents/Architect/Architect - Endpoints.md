# Architect Agent Endpoints

This document describes the **actual Architect API endpoints** and provides **fully expanded proposal examples** so frontend can implement UX without inferring missing fields.

## Base Router
- Router prefix: `/jobs/architect`
- Source: `shrecknet/app/api/routers/architect.py`

---

## 1) Start Analysis Run

### Endpoint
`POST /jobs/architect/{agent_id}/analyze`

### Purpose
Creates an Architect run and triggers async analysis task (`architect.analyze_instance`) to produce proposals.

### Request Body (`ArchitectAnalysisRequest`)
```json
{
  "ontology_instance_id": "instance-9f2e7f42",
  "ontology_id": 12,
  "max_chunks": 60,
  "chunk_size": 1000
}
```

### Response (`202`, `ArchitectAnalysisRunRead`)
Note: At creation time, run may still be `pending/running`; proposals may be empty until task completes.

```json
{
  "id": "run-4f61136f-0f7b-4e8d-b8e7-22fb19a45f5f",
  "agent_id": "9f0cf9da-4d3c-482f-b58e-3f9db2ff1d0c",
  "background_job_id": 781,
  "generation_job_id": null,
  "ontology_id": 12,
  "ontology_instance_id": "instance-9f2e7f42",
  "status": "running",
  "input_chunk_count": null,
  "settings": {
    "requested_by": 42,
    "max_chunks": 60,
    "chunk_size": 1000
  },
  "created_at": "2026-04-17T15:11:22.114000+00:00",
  "updated_at": "2026-04-17T15:11:22.114000+00:00",
  "proposals": []
}
```

---

## 2) Get Run With Proposals

### Endpoint
`GET /jobs/architect/runs/{run_id}`

### Purpose
Returns run status plus full proposals list after analysis.

### Response (`ArchitectAnalysisRunRead`) With Expanded Proposal Examples

```json
{
  "id": "run-4f61136f-0f7b-4e8d-b8e7-22fb19a45f5f",
  "agent_id": "9f0cf9da-4d3c-482f-b58e-3f9db2ff1d0c",
  "background_job_id": 781,
  "generation_job_id": null,
  "ontology_id": 12,
  "ontology_instance_id": "instance-9f2e7f42",
  "status": "completed",
  "input_chunk_count": 18,
  "settings": {
    "requested_by": 42,
    "max_chunks": 60,
    "chunk_size": 1000
  },
  "created_at": "2026-04-17T15:11:22.114000+00:00",
  "updated_at": "2026-04-17T15:11:34.402000+00:00",
  "proposals": [
    {
      "id": "prop-entity-new-1",
      "proposal_type": "new_instance",
      "status": "pending",
      "entity_definition_id": null,
      "entity_instance_id": null,
      "alias": "Mary",
      "confidence": 0.91,
      "justification": "Character is directly referenced and performs actions in multiple chunks",
      "evidence": null,
      "metadata": {
        "resolved_status": "new",
        "mention_count": 4,
        "chunk_indices": [0, 1, 2, 4],
        "ontology_name": "character"
      },
      "chunks": [],
      "merged_into_proposal_id": null,
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "generated_entity_instance_id": null,
      "created_at": "2026-04-17T15:11:34.338000+00:00",
      "updated_at": "2026-04-17T15:11:34.338000+00:00"
    },
    {
      "id": "prop-entity-update-1",
      "proposal_type": "update_instance",
      "status": "pending",
      "entity_definition_id": null,
      "entity_instance_id": "entity-1e1f7f9b",
      "alias": "The Old Tavern",
      "confidence": 0.86,
      "justification": "Existing location is referenced with new state details",
      "evidence": null,
      "metadata": {
        "resolved_status": "existing",
        "mention_count": 3,
        "chunk_indices": [0, 1, 3],
        "ontology_name": "location"
      },
      "chunks": [],
      "merged_into_proposal_id": null,
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "generated_entity_instance_id": null,
      "created_at": "2026-04-17T15:11:34.339000+00:00",
      "updated_at": "2026-04-17T15:11:34.339000+00:00"
    },
    {
      "id": "prop-scene-1",
      "proposal_type": "propose_scene",
      "status": "pending",
      "entity_definition_id": null,
      "entity_instance_id": null,
      "alias": "Tavern Confrontation",
      "confidence": 0.8,
      "justification": "A single continuous confrontation inside the tavern",
      "evidence": null,
      "metadata": {
        "proposal_kind": "scene",
        "scene_ref": "scene-8b7a69b2-bd42-4fd2-a6b5-2f1ee7f80ad8",
        "name": "Tavern Confrontation",
        "description": "Mary confronts John about betrayal in the tavern",
        "created_at": "2026-04-17T15:11:34.212000+00:00",
        "author": {
          "created_by_type": "agent",
          "created_by_author": "9f0cf9da-4d3c-482f-b58e-3f9db2ff1d0c"
        },
        "derived_from": {
          "entity_instance_id": "entity-8a4f2cb1"
        },
        "mentions": ["Mary", "John", "The Old Tavern"],
        "scene_order": 1
      },
      "chunks": [],
      "merged_into_proposal_id": null,
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "generated_entity_instance_id": null,
      "created_at": "2026-04-17T15:11:34.340000+00:00",
      "updated_at": "2026-04-17T15:11:34.340000+00:00"
    },
    {
      "id": "prop-milestone-1",
      "proposal_type": "propose_milestone",
      "status": "pending",
      "entity_definition_id": null,
      "entity_instance_id": null,
      "alias": "Confrontation begins",
      "confidence": 0.75,
      "justification": "Marks the start of conflict in-scene",
      "evidence": null,
      "metadata": {
        "proposal_kind": "milestone",
        "milestone_ref": "milestone-5f619cae-aec0-461f-8f5f-8fd3e65314e4",
        "scene_ref": "scene-8b7a69b2-bd42-4fd2-a6b5-2f1ee7f80ad8",
        "label": "Confrontation begins",
        "description": "Mary accuses John of betrayal",
        "boundary_type": "begin",
        "created_at": "2026-04-17T15:11:34.212000+00:00",
        "author": {
          "created_by_type": "agent",
          "created_by_author": "9f0cf9da-4d3c-482f-b58e-3f9db2ff1d0c"
        },
        "derived_from": {
          "entity_instance_id": "entity-8a4f2cb1"
        },
        "mentions": ["Mary", "John"],
        "milestone_order": 1
      },
      "chunks": [],
      "merged_into_proposal_id": null,
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "generated_entity_instance_id": null,
      "created_at": "2026-04-17T15:11:34.341000+00:00",
      "updated_at": "2026-04-17T15:11:34.341000+00:00"
    },
    {
      "id": "prop-milestone-2",
      "proposal_type": "propose_milestone",
      "status": "pending",
      "entity_definition_id": null,
      "entity_instance_id": null,
      "alias": "Confrontation ends",
      "confidence": 0.75,
      "justification": "Marks scene closure when John exits",
      "evidence": null,
      "metadata": {
        "proposal_kind": "milestone",
        "milestone_ref": "milestone-5fcb2f04-102d-4f9d-a811-4da5ec03f9ec",
        "scene_ref": "scene-8b7a69b2-bd42-4fd2-a6b5-2f1ee7f80ad8",
        "label": "Confrontation ends",
        "description": "John leaves the tavern",
        "boundary_type": "end",
        "created_at": "2026-04-17T15:11:34.212000+00:00",
        "author": {
          "created_by_type": "agent",
          "created_by_author": "9f0cf9da-4d3c-482f-b58e-3f9db2ff1d0c"
        },
        "derived_from": {
          "entity_instance_id": "entity-8a4f2cb1"
        },
        "mentions": ["John", "The Old Tavern"],
        "milestone_order": 3
      },
      "chunks": [],
      "merged_into_proposal_id": null,
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "generated_entity_instance_id": null,
      "created_at": "2026-04-17T15:11:34.342000+00:00",
      "updated_at": "2026-04-17T15:11:34.342000+00:00"
    },
    {
      "id": "prop-relates-to-1",
      "proposal_type": "propose_relates_to",
      "status": "pending",
      "entity_definition_id": null,
      "entity_instance_id": null,
      "alias": "milestone:milestone-5f619cae-aec0-461f-8f5f-8fd3e65314e4 -> Mary",
      "confidence": 0.84,
      "justification": "Milestone text explicitly names Mary as actor",
      "evidence": null,
      "metadata": {
        "proposal_kind": "relates_to",
        "source_ref": "milestone-5f619cae-aec0-461f-8f5f-8fd3e65314e4",
        "source_kind": "milestone",
        "target": {
          "kind": "new_entity_proposal",
          "alias": "Mary"
        },
        "relationship": "RELATES_TO",
        "confidence": 0.84,
        "evidence": "Milestone mentions Mary by name"
      },
      "chunks": [],
      "merged_into_proposal_id": null,
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "generated_entity_instance_id": null,
      "created_at": "2026-04-17T15:11:34.343000+00:00",
      "updated_at": "2026-04-17T15:11:34.343000+00:00"
    }
  ]
}
```

### Frontend UX Field Notes (Important)
- `proposal_type` drives card/component type:
  - `new_instance`, `update_instance`
  - `propose_scene`, `propose_milestone`, `propose_relates_to`
- `status` drives moderation state badge/action availability:
  - `pending`, `approved`, `rejected`, `merged`
- `metadata` is the detailed payload per type:
  - scene: `scene_ref`, `description`, `mentions`, `scene_order`, `derived_from`
  - milestone: `milestone_ref`, `scene_ref`, `boundary_type`, `milestone_order`, `mentions`
  - relates_to: `source_ref`, `source_kind`, `target`, `relationship`, `evidence`
- `corrected_*` fields are frontend curation outputs before generation.
- `generated_entity_instance_id` is populated after generation task (step 2) for applied entity proposals.

---

## 3) List Runs For Agent

### Endpoint
`GET /jobs/architect/{agent_id}/runs?limit=20&offset=0`

### Purpose
Returns run summaries for an Architect agent.

### Response (`ArchitectAnalysisRunSummary[]`)
```json
[
  {
    "id": "run-4f61136f-0f7b-4e8d-b8e7-22fb19a45f5f",
    "agent_id": "9f0cf9da-4d3c-482f-b58e-3f9db2ff1d0c",
    "background_job_id": 781,
    "generation_job_id": null,
    "ontology_id": 12,
    "ontology_instance_id": "instance-9f2e7f42",
    "status": "completed",
    "input_chunk_count": 18,
    "created_at": "2026-04-17T15:11:22.114000+00:00",
    "updated_at": "2026-04-17T15:11:34.402000+00:00"
  }
]
```

---

## 4) Patch Proposal Status In Bulk

### Endpoint
`PATCH /jobs/architect/runs/{run_id}/proposals/status`

### Request (`ArchitectProposalStatusUpdate`)
```json
{
  "proposal_ids": ["prop-entity-new-1", "prop-scene-1"],
  "status": "approved"
}
```

### Response
```json
{
  "updated": 2
}
```

---

## 5) Create Proposal (Manual Insert)

### Endpoint
`POST /jobs/architect/runs/{run_id}/proposals`

### Purpose
Allows frontend/admin tooling to insert a proposal manually.

### Request (all writable fields)
```json
{
  "proposal_type": "propose_scene",
  "status": "pending",
  "entity_definition_id": null,
  "entity_instance_id": null,
  "alias": "Bridge Scene",
  "confidence": 0.72,
  "justification": "Transition scene",
  "evidence": null,
  "metadata": {
    "proposal_kind": "scene",
    "scene_ref": "scene-9d1e1a0a-2b8a-4f56-b433-1d5219f3c2a1",
    "name": "Bridge Scene",
    "description": "Characters move from tavern to docks",
    "created_at": "2026-04-17T15:20:00.000000+00:00",
    "author": {
      "created_by_type": "agent",
      "created_by_author": "9f0cf9da-4d3c-482f-b58e-3f9db2ff1d0c"
    },
    "derived_from": {
      "entity_instance_id": "entity-8a4f2cb1"
    },
    "mentions": ["Mary", "John"],
    "scene_order": 2
  },
  "chunks": []
}
```

### Response
`ArchitectProposalRead` (same shape as proposal objects shown above).

---

## 6) Update Single Proposal

### Endpoints
- `PATCH /jobs/architect/runs/{run_id}/proposals/{proposal_id}`
- `PUT /jobs/architect/runs/{run_id}/proposals/{proposal_id}`

### Typical Frontend Curation Payload
```json
{
  "status": "approved",
  "corrected_alias": "Mary O'Neil",
  "corrected_entity_definition_id": 7,
  "corrected_proposal_type": "new_instance",
  "corrected_entity_instance_id": null,
  "metadata": {
    "ux_note": "Merged duplicate cards before approve"
  }
}
```

### Response
`ArchitectProposalRead` with updated fields.

---

## 7) Start Generation From Curated Proposals (Step 2)

### Endpoint
`POST /jobs/architect/runs/{run_id}/generate`

### Purpose
Starts async generation/update task from curated proposals.

### Request (`ArchitectValidationRequest`)
At least one of `validated_proposals` or `revised_suggestions` must be present.

#### Option A: `validated_proposals`
```json
{
  "run_id": "run-4f61136f-0f7b-4e8d-b8e7-22fb19a45f5f",
  "validated_proposals": [
    {
      "proposal_id": "prop-entity-new-1",
      "status": "approved",
      "corrected_alias": "Mary O'Neil",
      "corrected_entity_definition_id": 7,
      "corrected_proposal_type": "new_instance",
      "corrected_entity_instance_id": null,
      "merged_into_proposal_id": null
    },
    {
      "proposal_id": "prop-entity-update-1",
      "status": "approved",
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": "update_instance",
      "corrected_entity_instance_id": "entity-1e1f7f9b",
      "merged_into_proposal_id": null
    }
  ],
  "author_type": "user",
  "author_id": "42"
}
```

#### Option B: `revised_suggestions`
```json
{
  "run_id": "run-4f61136f-0f7b-4e8d-b8e7-22fb19a45f5f",
  "revised_suggestions": [
    {
      "suggestion_id": "prop-entity-new-1",
      "action": "new",
      "alias": "Mary O'Neil",
      "entity_definition_id": 7,
      "entity_instance_id": null,
      "chunk_indices": [0, 1, 2, 4],
      "merged_suggestion_ids": null,
      "merged_aliases": null,
      "status": "approved"
    }
  ],
  "author_type": "user",
  "author_id": "42"
}
```

### Response (`202`)
```json
{
  "status": "accepted",
  "task_id": "0b10d4f7-6d9c-4d9a-8b6b-cad2f34b6b86",
  "run_id": "run-4f61136f-0f7b-4e8d-b8e7-22fb19a45f5f",
  "message": "Entity generation task started"
}
```

---

## 8) Delete Run(s)

### Endpoints
- `DELETE /jobs/architect/{agent_id}/runs/{run_id}`
- `DELETE /jobs/architect/{agent_id}/runs`

### Responses
```json
{ "deleted": 1 }
```

or

```json
{ "deleted": 4 }
```

---

## Proposal Type Quick Reference (Frontend)

### `new_instance`
- Entity candidate not matched to existing node.
- Main UX actions: approve/reject/edit alias/entity_definition.

### `update_instance`
- Existing entity matched (`entity_instance_id` present).
- Main UX actions: approve/reject/re-target entity_instance.

### `propose_scene`
- Narrative segment container.
- Uses `metadata.scene_ref` as scene key in UI graph/timeline.

### `propose_milestone`
- Atomic in-scene event.
- Uses `metadata.scene_ref` + `metadata.milestone_ref` + `metadata.boundary_type`.

### `propose_relates_to`
- Edge proposal from scene/milestone to entity.
- Target in `metadata.target`:
  - existing entity: `{ kind: "existing_entity", entity_instance_id, alias }`
  - new proposal: `{ kind: "new_entity_proposal", alias }`

---

## Notes For UX Robustness
- Do not assume every proposal has `entity_definition_id`/`entity_instance_id`; scene/milestone/relates_to proposals commonly have `null` there.
- Treat `metadata` as required-by-type payload. Render fallback only if absent.
- Track lifecycle with `status`; do not infer approval from confidence.
- Use `id` as stable proposal key, not alias.
- Run-level `status` and proposal-level `status` are different scopes.
- For generation step, non-entity proposals are ignored by generation task by design.
