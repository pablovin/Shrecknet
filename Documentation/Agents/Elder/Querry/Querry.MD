# Elder Querry

This document describes the technical behavior of Elder query execution.

Note: file name keeps `Querry` for compatibility with requested path.

## Endpoint

- `POST /jobs/elder/{agent_id}/query`
- Stream-compatible: `POST /chat/messages/stream`

## Input Contract

Request model: `ElderQueryRequest`

Core fields:

- `query`
- `top_k`
- `include_trace`
- `fast`
- `route` (`auto | fast | deep`)
- `chat_id` (optional memory context)
- `candidate_limit`
- `rerank_limit`
- legacy `node_scope` (kept for compatibility)

## Decomposition Contract

The decomposition stage returns intent objects:

- `subquery`: focused retrieval question
- `target_data_type`: `entity | scene | milestone | mixed`
- `reason`: short explanation
- `top_k_entities`: retrieved `EntityInstance` node IDs for this subquery
- `top_k_scenes`: retrieved `Scene` node IDs for this subquery
- `top_k_milestones`: retrieved `Milestone` node IDs for this subquery

### Fast Mode Relation

`fast`/`route` use the same Elder response contract and downstream retrieval/synthesis stages, but change planning:

- `route=fast`: creates exactly one intent
  - `subquery`: original user query
  - `target_data_type`: `mixed`
  - `reason`: `fast_mode`
- `route=deep`: plan first, then execute bounded retrieval steps.
- `route=auto`: fast-first pass, and expands to decomposition only when first-pass retrieval quality is low

Backward compatibility in current implementation:

- if `route=auto` and `fast=false`, request is treated as deep.

This means fast mode is not a different API contract; it is a reduced planning path for lower latency.

This decomposition supports query-type routing:

- `entity` for who/what questions
- `scene` for what-happened-in-context questions
- `milestone` for arc/timeline progression questions

## Retrieval Contract

Each intent is retrieved in parallel (bounded concurrency).

For each retrieval, Elder records counters:

- `raw_candidates`
- `after_parent_grouping`
- `after_dedup`
- `final_k`

Node survival rule:

- Deduplication is node-level (`node_id`) to avoid dropping distinct nodes from the same instance.

## Memory Priors Contract

Memory is applied as explicit biasing priors, not freeform rewrite:

- `entity_prior`
- `temporal_prior`
- `disambiguation_prior`
- `continuity_prior`

Each prior in output includes:

- `type`
- `effect`
- `targets`
- `why`
- `impact_on_scores`

## Output Contract

Response model: `ElderQueryResponse`

Top-level fields:

- `agent_id`
- `query`
- `answer`
- `timings`
- `sources`
- `memory_priors_applied`
- `trace_id`
- optional `trace`
- optional `retrieval_debug`

`sources[]` node payload:

- `node_id`
- `node_label` (`EntityInstance | Scene | Milestone`)
- `node_name`
- `score`
- `evidence_chunks[]` with `chunk_id`, `chunk_type`, `score`, `text`

## Timing Contract

Elder logs and returns end-to-end timing fields:

- `decompose_ms`
- `memory_summary_ms`
- `retrieve_ms`
- `consolidate_ms`
- `rerank_ms`
- `synthesize_ms`
- `total_ms`

Timing interpretation with fast mode:

- `decompose_ms` is typically near-zero/minimal in fast mode
- retrieval/synthesis timings remain fully measured and returned

## Frontend Notes

- Use `sources` to render provenance/explainability.
- Use `timings.total_ms` and step timings for UX and debugging.
- Use `trace_id` to correlate frontend issues with backend logs.
- Optional `trace` can be shown in debug mode.
