# Retrieval Endpoints for Frontend

Base paths:

- `/graphrag`
- `/jobs/elder`

Auth:

- Bearer token required

## GraphRAG Retrieval

### Semantic search

- `POST /graphrag/search`

Request body:

```json
{
  "query": "what happened to the warden at the marsh gate?",
  "ontology_id": 12,
  "k": 10,
  "node_scope": "milestone",
  "candidate_limit": 40,
  "rerank_limit": 20
}
```

`node_scope` supports:

- `everything`
- `entity`
- `scene`
- `milestone`
- `mixed`

Response shape (concise):

```json
{
  "query": "what happened to the warden at the marsh gate?",
  "total": 2,
  "ontology_id": 12,
  "results": [
    {
      "node_id": "scene_opening",
      "name": "Opening at the Marsh Gate",
      "labels": ["Scene"],
      "score": 0.86,
      "context_text": "...",
      "chunk_score": 0.86,
      "node_score": 0.79,
      "importance_index": 0.83,
      "matched_chunk_count": 3,
      "score_breakdown": {
        "vector_best": 0.86,
        "chunk_coverage": 0.6,
        "top_avg": 0.81,
        "keyword_overlap": 0.5,
        "exact_or_fuzzy": 1.0,
        "node_type_prior": 0.03,
        "graph_total_boost": 0.04
      },
      "graph_boost": 0.04,
      "evidence_bundle": {
        "parent_type": "Scene",
        "parent_id": "scene_opening",
        "parent_name": "Opening at the Marsh Gate"
      }
    }
  ],
  "evidence_bundles": [
    {
      "parent_type": "Scene",
      "parent_id": "scene_opening",
      "parent_name": "Opening at the Marsh Gate"
    }
  ]
}
```

### LLM context retrieval

- `POST /graphrag/context`

Request body:

```json
{
  "query": "summarize the marsh gate negotiation",
  "ontology_id": 12,
  "k": 5,
  "node_scope": "scene"
}
```

## Elder Retrieval

### Elder query execution

- `POST /jobs/elder/{agent_id}/query`

Request body (key fields):

```json
{
  "query": "who discovered the sigil and in which scene?",
  "synthesis_evidence_budget_tokens": 100000,
  "candidate_limit": 40,
  "rerank_limit": 20,
  "include_trace": false,
  "chat_id": "optional-chat-id"
}
```

Response shape (current):

```json
{
  "agent_id": "elder_01",
  "query": "who discovered the sigil and in which scene?",
  "answer": "Riven discovered the sigil in Gatehouse Confrontation...",
  "timings": {
    "decompose_ms": 61.2,
    "memory_summary_ms": 15.4,
    "retrieve_ms": 148.8,
    "consolidate_ms": 23.1,
    "rerank_ms": 18.5,
    "synthesize_ms": 201.3,
    "total_ms": 468.9
  },
  "sources": [
    {
      "node_id": "ent_9b2",
      "node_label": "EntityInstance",
      "node_name": "Riven",
      "score": 0.89,
      "evidence_chunks": [
        {
          "chunk_id": "c1",
          "chunk_type": "text",
          "score": 0.91,
          "text": "..."
        }
      ]
    }
  ],
  "memory_priors_applied": [
    {
      "type": "entity_prior",
      "effect": "boost",
      "targets": ["ent_9b2"],
      "why": "recently discussed entities in chat history",
      "impact_on_scores": 0.03
    }
  ],
  "trace_id": "elder-trace-uuid",
  "trace": null,
  "retrieval_debug": []
}
```

### Chat stream-compatible entrypoint

- `POST /chat/messages/stream`

The older `/jobs/elder/chat/messages/stream` spelling appeared in previous documentation but
is not the registered route.

Same retrieval controls apply (`candidate_limit`, `rerank_limit`,
`synthesis_evidence_budget_tokens`, `chat_id`, `include_trace`).

Both Elder endpoints use `elder-query-retrieval-v2` by default. `instance_id` is an optional,
additive request field. Responses include `pipeline_version`.

`synthesis_evidence_budget_tokens` defaults to `100000` and accepts
`1000..1000000`. Elder hydrates sources in retrieval order without per-source
text truncation. It adds full sources until the aggregate budget is crossed,
includes the complete source that crosses it, and then stops. The actual evidence
count may therefore exceed the soft budget by one source.

## Notes

- Elder response is source-grounded and includes explicit provenance in `sources`.
- `trace_id` can be used to correlate frontend behavior with backend logs.
