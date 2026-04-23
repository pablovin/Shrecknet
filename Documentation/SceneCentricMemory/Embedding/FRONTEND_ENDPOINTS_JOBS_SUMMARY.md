# Embedding Endpoints for Frontend

Purpose: focused reference for ontology embedding endpoints only.

Current embedding scope:

- Embedded node types: `EntityInstance`, `Scene`, `Milestone`

## Lifecycle Notes

- Retrieval uses `EntityChunk` vectors (`entity_chunk_vec_idx`).
- Scene/milestone writes now use coalesced reconciliation embedding triggers in backend flows to reduce queue fanout.
- Reconciliation strategy favors targeted refresh + instance reconciliation over repeated broad full-ontology fanout.

## GET /ontologies/{ontology_id}/embedding-stats

Returns aggregate and per-type embedding counts.

Request example:

```http
GET /ontologies/12/embedding-stats
Authorization: Bearer <token>
```

Response example:

```json
{
  "ontology_id": 12,
  "total_nodes": 132,
  "embedded_nodes": 127,
  "unembedded_nodes": 5,
  "outdated_nodes": 2,
  "entities": {
    "total": 100,
    "embedded": 99,
    "unembedded": 1,
    "outdated": 1
  },
  "scenes": {
    "total": 20,
    "embedded": 18,
    "unembedded": 2,
    "outdated": 1
  },
  "milestones": {
    "total": 12,
    "embedded": 10,
    "unembedded": 2,
    "outdated": 0
  }
}
```

## POST /ontologies/{ontology_id}/trigger-embedding

Triggers an ontology embedding job.

Request example:

```http
POST /ontologies/12/trigger-embedding
Authorization: Bearer <token>
Content-Type: application/json

{}
```

Response example:

```json
{
  "job_id": "d1c1c2d3-6d7e-4e59-9d9a-0f1a2b3c4d5e",
  "ontology_id": 12,
  "message": "Embedding job triggered for ontology 12",
  "requested_entities": 100,
  "requested_scenes": 20,
  "requested_milestones": 12
}
```

## GET /ontologies/{ontology_id}/embedding-jobs?limit=10

Returns recent ontology embedding jobs.

Request example:

```http
GET /ontologies/12/embedding-jobs?limit=10
Authorization: Bearer <token>
```

Response example:

```json
[
  {
    "kind": "neo4j_embedding",
    "job_id": "424",
    "start_time": "2026-04-17T14:11:19.313000+00:00",
    "status": "done",
    "author_type": "user",
    "author_id": "7",
    "description": "Embedding nodes for ontology 12",
    "details": {
      "ontology_id": 12,
      "processed_by_type": {
        "entities": 38,
        "scenes": 7,
        "milestones": 11
      }
    },
    "progress": 1.0,
    "error_message": null,
    "completed_at": "2026-04-17T14:11:35.923000+00:00",
    "duration_seconds": 16.61,
    "ontology_id": 12,
    "updated_at": "2026-04-17T14:11:35.923000+00:00"
  }
]
```

## Queue Metrics (Backend-facing)

Coalesced reconciliation jobs expose queue metrics in task outputs/logs:

- `jobs_enqueued`
- `jobs_coalesced`
- `avg_nodes_per_job`
- `fanout_per_request`

These are useful for latency/load monitoring dashboards.
