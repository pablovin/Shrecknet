# Scene-Centric Retrieval

Purpose: concise technical reference for current retrieval behavior in GraphRAG and Elder.

## Scope

Retrieval operates on embedded parents of type:

- `EntityInstance`
- `Scene`
- `Milestone`

Label-target filtering supports:

- `entity`
- `scene`
- `milestone`
- `mixed` (all)

## Retrieval Pipeline (Current)

### 1. Query Construction

- Elder decomposes user question into `1..10` intents.
- Each intent includes:
  - `subquery`
  - `target_data_type`
  - `reason`
  - `top_k_entities`
  - `top_k_scenes`
  - `top_k_milestones`

### 2. Candidate Generation

- Query is embedded.
- Neo4j vector search runs on `EntityChunk` vectors (`entity_chunk_vec_idx`).
- Candidate window controls:
  - `candidate_limit`
  - `rerank_limit`
- Label filtering uses the intent target type.

### 3. Candidate Consolidation

- Chunk candidates are grouped to parent nodes.
- Dedup is node-level (by `node_id`) for Elder consolidation.
- Top evidence chunks are attached per node.

### 4. Reranking

- Deterministic node scoring combines:
  - best chunk score
  - chunk coverage
  - top-chunk average
  - keyword overlap
  - exact/fuzzy signal
  - node-type prior
- Graph boosts are additive and bounded.
- Memory priors (Elder) can bias ranking:
  - `entity_prior`
  - `temporal_prior`
  - `disambiguation_prior`
  - `continuity_prior`

### 5. Grounded Synthesis (Elder)

- Final answer is synthesized from consolidated node evidence.
- Output includes explicit `sources` for provenance.

## Retrieval Debug Counters

Per intent retrieval includes:

- `raw_candidates`
- `after_parent_grouping`
- `after_dedup`
- `final_k`

## Additive Retrieval Fields (GraphRAG Node Results)

Per result node/chunk:

- `chunk_score`
- `node_score`
- `importance_index`
- `matched_chunk_count`
- `score_breakdown`
- `graph_boost`
- `evidence_bundle`

Top-level GraphRAG response:

- `evidence_bundles`
- `debug_stats` (includes retrieval counters and effective allowed labels)

## Elder Response Grounding

Elder now returns source-grounded payload:

- `intents[]`
  - includes per-intent retrieval links: `top_k_entities|top_k_scenes|top_k_milestones`
- `sources[]` (node-backed evidence)
- `memory_priors_applied[]`
- `timings`
- `trace_id`

## Latency Visibility

Elder timing fields:

- `decompose_ms`
- `memory_summary_ms`
- `retrieve_ms`
- `consolidate_ms`
- `rerank_ms`
- `synthesize_ms`
- `total_ms`
