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
  "node_scope": "everything",
  "candidate_limit": 40,
  "rerank_limit": 20
}
```

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
        "chunk_coverage": 0.60,
        "top_avg": 0.81,
        "keyword_overlap": 0.50,
        "exact_or_fuzzy": 1.00,
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

Response shape:
```json
{
  "query": "summarize the marsh gate negotiation",
  "ontology_id": 12,
  "context": "Formatted retrieval context..."
}
```

## Elder Retrieval

### Elder query execution
- `POST /jobs/elder/{agent_id}/query`

Request body (retrieval-relevant fields):
```json
{
  "query": "who discovered the sigil and in which scene?",
  "mode": "both",
  "top_k": 8,
  "node_scope": "everything",
  "candidate_limit": 40,
  "rerank_limit": 20,
  "include_trace": false
}
```

Response retrieval fields (inside `context[]` and each `subanswers[].retrieval[]` item):
- `chunk_score`
- `node_score`
- `importance_index`
- `matched_chunk_count`
- `score_breakdown`
- `graph_boost`
- `evidence_bundle`

### Chat stream-compatible entrypoint
- `POST /jobs/elder/chat/messages/stream`

Same retrieval controls apply in request body:
- `node_scope`
- `candidate_limit`
- `rerank_limit`

## Field Constraints

`node_scope`:
- `everything`
- `entity`
- `scene`

`candidate_limit`:
- integer, `5..200`

`rerank_limit`:
- integer, `1..100`

## Notes

- New retrieval fields are additive and backward-compatible.
- Existing fields and legacy consumers remain valid.
