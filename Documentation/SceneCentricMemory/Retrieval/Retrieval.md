# Scene-Centric Retrieval

Purpose: concise technical reference for current retrieval behavior in GraphRAG and Elder.

## Scope

Retrieval operates on embedded parents of type:
- `EntityInstance`
- `Scene`
- `Milestone`

Query-time scope filter:
- `node_scope = everything | entity | scene`

## Retrieval Pipeline (actual flow)

1. Query embedding
- The raw query is embedded using the configured embedding model.

2. Vector candidate fetch
- Neo4j vector search runs on chunk vectors (`entity_chunk_vec_idx`).
- Candidate window is controlled by:
  - `candidate_limit` when provided
  - otherwise internal default window based on `k`

3. Parent filtering
- Candidates are projected from chunk -> parent node.
- Parents are filtered by:
  - allowed labels from `node_scope`
  - optional `ontology_id`

4. Deterministic node scoring
- Chunk matches are grouped per parent node.
- Node score is computed from stable signals:
  - best chunk score
  - chunk coverage
  - top chunk average
  - keyword overlap
  - exact/fuzzy match signal
  - node-type prior

5. Rerank window and graph enrichments
- Node rerank window uses `rerank_limit` (or default window).
- Neighbor traversal adds graph-aware boosts and evidence assembly.
- Additive graph boosts are bounded and merged into `importance_index`.

6. Final top-k
- Results are sorted by boosted `importance_index`.
- Final response is truncated to requested `k`.

## Additive Retrieval Fields

Per result node/chunk:
- `chunk_score`: best chunk-level similarity for the node
- `node_score`: deterministic pre-boost node score
- `importance_index`: final ranking score after additive boosts
- `matched_chunk_count`: matched chunk count for the node
- `score_breakdown`: deterministic scoring + graph boost components
- `graph_boost`: additive bounded graph boost
- `evidence_bundle`: structured supporting graph evidence

Top-level GraphRAG response:
- `evidence_bundles`: list of non-null evidence bundles from returned results

## Elder Consumption

Elder retrieval now forwards and returns the same additive scoring/evidence fields so downstream UX can:
- display ranking rationale
- surface evidence context
- tune retrieval windows per query (`candidate_limit`, `rerank_limit`)

## Compatibility

All retrieval additions are additive.
Existing request/response fields remain valid and unchanged.
