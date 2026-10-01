# Architect Agent Endpoints (Current API)

This document reflects the real endpoints implemented in:
- shrecknet/app/api/routers/architect.py

Router prefix:
- /jobs/architect

Authentication:
- All endpoints require authenticated user context.

## 1. Start Analysis

Endpoint:
- POST /jobs/architect/{agent_id}/analyze

Purpose:
- Creates an architect run and starts async analysis.

Request body (ArchitectAnalysisRequest):
```json
{
  "ontology_instance_id": "28d4658b-30f5-4789-b5bb-b319e9e6e471",
  "ontology_id": 1,
  "max_chunks": 50,
  "chunk_size": 1000
}
```

Response:
- 202 Accepted
- ArchitectAnalysisRunRead

Common errors:
- 404 Agent not found
- 400 Agent is not active
- 400 Agent job type is not architect
- 503 OpenAI API key not configured

## 2. Get Run Details

Endpoint:
- GET /jobs/architect/runs/{run_id}

Purpose:
- Returns one run with proposals.

Response:
- 200 OK
- ArchitectAnalysisRunRead

Common errors:
- 404 Architect run not found

## 3. List Runs for Agent

Endpoint:
- GET /jobs/architect/{agent_id}/runs?limit=20&offset=0

Purpose:
- Lists runs for an architect agent.

Query params:
- limit: integer, min 1, max 100, default 20
- offset: integer, min 0, default 0

Response:
- 200 OK
- list of ArchitectAnalysisRunSummary

Common errors:
- 404 Agent not found
- 400 Agent job type is not architect

## 4. Delete One Run

Endpoint:
- DELETE /jobs/architect/{agent_id}/runs/{run_id}

Response:
```json
{
  "deleted": 1
}
```

Common errors:
- 404 Architect run not found

## 5. Delete All Runs for Agent

Endpoint:
- DELETE /jobs/architect/{agent_id}/runs

Response:
```json
{
  "deleted": 4
}
```

## 6. Bulk Update Proposal Status

Endpoint:
- PATCH /jobs/architect/runs/{run_id}/proposals/status

Request body (ArchitectProposalStatusUpdate):
```json
{
  "proposal_ids": [
    "0a7b0d2a-2a8d-4a19-86a8-1c285adf2c9f",
    "43b72243-c88a-4317-b9d6-e879dc879fa4"
  ],
  "status": "approved"
}
```

Response:
```json
{
  "updated": 2
}
```

Common errors:
- 404 Architect run not found

## 7. Create Proposal in Run

Endpoint:
- POST /jobs/architect/runs/{run_id}/proposals

Request body:
- Same shape as ArchitectProposalRead (id and timestamps optional; backend can generate id).

Example:
```json
{
  "proposal_type": "new_instance",
  "status": "pending",
  "entity_definition_id": 18,
  "entity_instance_id": null,
  "alias": "Londinium",
  "confidence": 0.86,
  "justification": "Detected location mention",
  "evidence": [],
  "metadata": {
    "source": "manual"
  },
  "chunks": ["Rome withdrew from Britain..."],
  "merged_into_proposal_id": null,
  "corrected_alias": null,
  "corrected_entity_definition_id": null,
  "corrected_proposal_type": null,
  "corrected_entity_instance_id": null,
  "generated_entity_instance_id": null
}
```

Response:
- 201 Created
- ArchitectProposalRead

## 8. Update Proposal (Patch)

Endpoint:
- PATCH /jobs/architect/runs/{run_id}/proposals/{proposal_id}

## 9. Update Proposal (Put)

Endpoint:
- PUT /jobs/architect/runs/{run_id}/proposals/{proposal_id}

Purpose for both:
- Updates mutable proposal fields.

Example request:
```json
{
  "status": "approved",
  "corrected_alias": "Archbishop Dubricus",
  "corrected_entity_definition_id": 16,
  "corrected_proposal_type": "update_instance",
  "corrected_entity_instance_id": "320842e1-4aa4-4aa4-b4d3-afc9edf87bd5",
  "proposal_metadata": {
    "reviewed_by": "frontend"
  }
}
```

Response:
- 200 OK
- ArchitectProposalRead

Common errors:
- 404 Architect run not found
- 404 Architect proposal not found

## 10. Start Generation

Endpoint:
- POST /jobs/architect/runs/{run_id}/generate

Purpose:
- Starts async generation task based on reviewed pipeline output.

Request body (ArchitectGenerationRequest):
```json
{
  "run_id": "ad1111a5-6a83-4f2b-b325-391ce6b56cf3",
  "author_type": "user",
  "author_id": "42",
  "reviewed_pipeline_output": {
    "run_id": "ad1111a5-6a83-4f2b-b325-391ce6b56cf3",
    "ontology_instance_id": "28d4658b-30f5-4789-b5bb-b319e9e6e471",
    "outputs": {
      "entity_proposals": [],
      "scenes": [],
      "milestones": []
    }
  }
}
```

Response:
- 202 Accepted
```json
{
  "status": "accepted",
  "task_id": "0b10d4f7-6d9c-4d9a-8b6b-cad2f34b6b86",
  "run_id": "ad1111a5-6a83-4f2b-b325-391ce6b56cf3",
  "message": "Entity generation task started"
}
```

Validation rules:
- Path run_id must match body run_id.
- body.run_id must match reviewed_pipeline_output.run_id.

Compatibility note:
- Generation currently accepts outputs.scene_proposals or outputs.scenes.
- Generation currently accepts outputs.milestones_per_scene, outputs.milestone_proposals, or outputs.milestones.

## 9. Review Entity Catalog

All endpoints below require an authenticated user, matching existing ontology-instance reads.

### List entities

- `GET /ontologies/{ontology_id}/architect-review/entity-catalog?entity_definition_id=42&query=robert&limit=50`
- `entity_definition_id` and `query` are optional. `query` is case-insensitive normalized-name prefix matching.
- `cursor` is opaque and must be passed unchanged to fetch the next stable, name-ordered page.
- `limit` defaults to 50 and is between 1 and 200.

The response is intentionally flat. It does not include entity text, properties, or relationships:

```json
{
  "results": [{
    "entity_instance_id": "entity-uuid",
    "entity_definition_id": 42,
    "alias": "Robert Heinlein",
    "instance_id": "instance-uuid",
    "instance_name": "Authors",
    "avatar_url": null,
    "has_agent": true,
    "agent_id": "agent-uuid",
    "agent_name": "Julia",
    "agent_avatar_url": null
  }],
  "next_cursor": "WyJyb2JlcnQgaGVpbmxlaW4iLCJlbnRpdHktdXVpZCJd"
}
```

### Resolve proposal references

- `POST /ontologies/{ontology_id}/architect-review/entity-catalog/resolve`

```json
{"entity_instance_ids": ["entity-uuid", "another-entity-uuid"]}
```

Results retain first-seen request order. Duplicate IDs are removed. Missing IDs and IDs from another ontology are returned in `missing_entity_instance_ids` without exposing their data.

## 10. Compact Run Progress

- `GET /jobs/architect/runs/{run_id}/progress`
- `GET /jobs/architect/{agent_id}/runs?include=progress`

The single-run endpoint returns run status/timestamps, proposal counts by status, and compact analysis/generation job state. The list endpoint preserves its existing summary fields and adds that object as `progress` for every returned run.

```json
{
  "id": "run-uuid",
  "agent_id": "agent-uuid",
  "ontology_id": 1,
  "ontology_instance_id": "instance-uuid",
  "status": "running",
  "created_at": "2026-10-01T10:00:00Z",
  "updated_at": "2026-10-01T10:02:00Z",
  "proposal_counts": {"pending": 8, "approved": 2},
  "analysis_job": {
    "availability": "available",
    "job_id": 123,
    "status": "running",
    "progress": 0.65,
    "details": {"stage": "entity_extraction"},
    "error_message": null,
    "started_at": "2026-10-01T10:00:01Z",
    "completed_at": null,
    "updated_at": "2026-10-01T10:02:00Z"
  },
  "generation_job": {"availability": "not_started", "job_id": null, "status": null, "progress": null, "details": null, "error_message": null, "started_at": null, "completed_at": null, "updated_at": null}
}
```

`availability` is `not_started` when no job has been attached, `available` when the job record exists, and `unavailable` when an old attached job was purged. Clients must treat `unavailable` as terminal for polling and must not request `/jobs/{job_id}`.

## Enum Notes

Common proposal status values:
- pending
- approved
- rejected
- merged
- approved_with_updates
- disapproved

Common proposal type values:
- new_instance
- update_instance

Use values from app.models.architect enums as source of truth when in doubt.
