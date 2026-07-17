# Semantic Embedding V2 Endpoints

Existing frontend contracts remain in place. All operations now target `SemanticDocument` V2.

## GET `/ontologies/{ontology_id}/embedding-stats`

Returns the existing aggregate and per-type fields for canonical `EntityInstance`, `Scene`, and `Milestone` sources. A source is embedded when it owns a current V2 semantic document set.

```json
{
  "ontology_id": 12,
  "total_nodes": 132,
  "embedded_nodes": 127,
  "unembedded_nodes": 5,
  "outdated_nodes": 0,
  "entities": {"total": 100, "embedded": 99, "unembedded": 1, "outdated": 0},
  "scenes": {"total": 20, "embedded": 18, "unembedded": 2, "outdated": 0},
  "milestones": {"total": 12, "embedded": 10, "unembedded": 2, "outdated": 0}
}
```

Ontology vocabulary documents are additional derived documents and do not inflate canonical-node totals.

## POST `/ontologies/{ontology_id}/trigger-embedding`

Queues `ontology.embed_ontology`. The existing response fields are preserved:

```json
{
  "job_id": "celery-task-id",
  "ontology_id": 12,
  "message": "Embedding job triggered for ontology 12",
  "requested_entities": 100,
  "requested_scenes": 20,
  "requested_milestones": 12
}
```

The worker renders all expected V2 documents, hash-gates inference, removes obsolete V2 projections, and refreshes source flags.

## GET `/ontologies/{ontology_id}/embedding-jobs?limit=10`

Returns the existing background-job records. V2 job details may additionally report requested, embedded, and reused semantic-document counts.

## POST `/graphrag/embed/ontology/reset`

The existing reset request and response contract is preserved. Reset removes derived `SemanticDocument` data for the ontology without deleting canonical graph memory.

## Targeted automatic jobs

Canonical writes continue to enqueue the existing `ontology.embed_nodes`, `ontology.embed_instance`, or coalesced `ontology.embed_reconciliation` task names. Their implementations now call V2. SQL ontology-definition changes enqueue `ontology.embed_definitions` after commit.
