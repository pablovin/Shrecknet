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
- `include_trace`
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

### Adaptive planner

Elder uses one adaptive planning path. Each terminal evidence-producing step must
set `evidence_type`; resolution, constraint, and intermediate steps must omit it.

This decomposition supports query-type routing:

- `entity` for who/what questions
- `scene` for what-happened-in-context questions
- `milestone` for arc/timeline progression questions

## Retrieval Contract

Each intent is retrieved in parallel (bounded concurrency).

The internal retrieval-plan step supports:

- `temporal.ordering`: `relevance | recency`
- `temporal.direction`: `ascending | descending`
- `limit`: planner-selected evidence count

For recency, Elder orders by `updated_at` and then `created_at`. Missing timestamps
are marked non-comparable and placed after comparable records. Local
`FOLLOWED_BY`/`PRECEDED_BY` relationships never establish chronology between
different sources. These controls do not add fields to `ElderQueryRequest`.

Synthesis uses full, atomic sources. Evidence types map to server-owned targets:
`brief_fact` 12k, `relationship_or_local_event` 20k, `standard_summary` 35k,
`timeline_or_history` 60k, and `deep_comparison_or_mixed` or `exhaustive` 100k.
The source crossing a terminal step's target is included in full, and collection
for that step then stops. There is no
adjacent-chunk window or per-source 1,200-token truncation.

For each retrieval, Elder records counters:

- `raw_candidates`
- `after_parent_grouping`
- `after_dedup`
- `final_k`

Node survival rule:

- Deduplication is node-level (`node_id`) to avoid dropping distinct nodes from the same instance.
- Explicit temporal ranks survive deduplication and are not replaced by relevance
  ordering before synthesis.
- There is no request-level or server-level global result cap. Each planner step
  chooses its own bounded `limit`, and all selected step evidence proceeds to
  consolidation.

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

`answer` is display-ready cohesive character prose. Unicode superscript
citation numbers refer to the corresponding one-based entry in `sources[]`.
The response never exposes the internal `{cite ...}` wrapper format.

`sources[]` node payload:

- `node_id`
- `node_label` (`EntityInstance | Scene | Milestone`)
- `node_name`
- `score`
- `evidence_chunks[]` with `chunk_id`, `chunk_type`, `score`, `text`

The former `complete` fields were removed from source and evidence-chunk payloads.
Returned sources are the sources actually supplied to synthesis.

## Timing Contract

Elder logs and returns end-to-end timing fields:

- `decompose_ms`
- `memory_summary_ms`
- `retrieve_ms`
- `consolidate_ms`
- `rerank_ms`
- `synthesize_ms`
- `total_ms`

Before every Elder LLM call, the API logs an `[ELDER_LLM_REQUEST]` operational
header containing:

- `stage` derived from the Elder usage tag (`plan`, `synthesize`, or an overflow stage)
- `provider` and `model`
- `input_tokens_est`, estimated before submission using a provider-independent
  character heuristic

After the model responds, `[ELDER_LLM_RESPONSE]` reports the stage, resolved
provider/model, actual provider token usage when available, and `wait_ms`.
The response's `llm_usage[]` rows and the final `[ELDER_LLM_USAGE]` summary also
include `wait_ms`. The preflight token count is explicitly an estimate; provider
usage remains authoritative after completion.

The shrecknet HTTP client never resubmits a queued chat job after its polling
deadline. A submitted job continues inside shreckLLM, so resubmission would
create a concurrent duplicate and could duplicate provider charges. Provider
retries remain exclusively owned and reported by shreckLLM.

Timing interpretation with fast mode:

- `decompose_ms` is typically near-zero/minimal in fast mode
- retrieval/synthesis timings remain fully measured and returned

## Frontend Notes

- Use `sources` to render provenance/explainability.
- Use `timings.total_ms` and step timings for UX and debugging.
- Use `trace_id` to correlate frontend issues with backend logs.
- Optional `trace` can be shown in debug mode.
