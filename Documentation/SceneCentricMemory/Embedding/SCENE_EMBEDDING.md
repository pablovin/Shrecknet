# Scene-Centric Embedding

This document describes the current embedding strategy and the ontology embedding endpoints.

## Strategy (Current)

Embedding targets are graph memory nodes:

- `EntityInstance`
- `Scene`
- `Milestone`

These are embedded into retrieval chunks (`EntityChunk.text_embedding`). Nodes are considered for embedding when they are:

- not embedded yet (`is_embedded` false/null), or
- outdated (`last_updated_date > last_embedded_date`)

## Reconciliation and Fanout Control

The backend uses a coalesced reconciliation task (`ontology.embed_reconciliation`) for high-churn write paths.

- Optional targeted phase: embed specific `node_ids`.
- Optional instance phase: embed all nodes under one `instance_id`.
- One reconciliation job can cover both phases.
- Job details include queue fanout metrics:
  - `jobs_enqueued`
  - `jobs_coalesced`
  - `avg_nodes_per_job`
  - `fanout_per_request`

This replaces broad per-write fanout patterns with batched reconciliation.

## Ontology Embedding Endpoints

Implemented in `shrecknet/app/api/routers/ontologies.py`.

### 1) `GET /ontologies/{ontology_id}/embedding-stats`

Returns aggregate and per-type counters.

Behavior notes:

- Requires authenticated user.
- If SQL has entities but Neo4j has not been populated yet, totals are adjusted so missing graph nodes are counted as unembedded entities.

### 2) `POST /ontologies/{ontology_id}/trigger-embedding`

Queues a full ontology embedding job (`ontology.embed_ontology`).

Behavior notes:

- Requires role `admin` or `world_builder`.
- Response `job_id` is Celery task id.
- Response includes requested counts:
  - `requested_entities`
  - `requested_scenes`
  - `requested_milestones`

### 3) `GET /ontologies/{ontology_id}/embedding-jobs?limit=10`

Returns recent embedding jobs for that ontology from background-job storage.

Behavior notes:

- Requires authenticated user.
- `limit` is capped to 10 server-side.
- Output is frontend job format (`kind`, `job_id`, `status`, `details`, `progress`, etc.).
