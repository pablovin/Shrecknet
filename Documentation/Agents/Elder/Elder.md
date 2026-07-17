# Elder Agent — Query and Retrieval V2

This document describes the sole supported Elder query and retrieval system.

## V2 Contract

The normal path makes two LLM calls: one retrieval-plan call (at most five controlled
operations), deterministic dependency-wave retrieval, and one Elder synthesis call.
The synthesis call receives the complete hydrated evidence records, including canonical
node properties, every selected semantic document, provenance, and temporal metadata.
Evidence is never shortened into a preview or silently cut off.

If multiple complete records exceed the model context they are processed in complete-record
batches and combined by an additional synthesis pass. A single record too large for one
model call produces a typed capacity failure instead of partial evidence.

## Jobs Overview

Elder is a retrieval-grounded question answering pipeline over scene-centric memory.

Main endpoint:

- `POST /jobs/elder/{agent_id}/query`

Chat stream-compatible endpoint:

- `POST /chat/messages/stream`

## Goal

Given a user question, Elder returns a grounded answer plus explicit source nodes (`EntityInstance`, `Scene`, `Milestone`) used to build the response.

The Elder v2 architecture is:

1. Ontology, instance, entity, and conversation grounding
2. One validated retrieval plan with at most five operations
3. Parallel deterministic retrieval waves
4. Unified deduplicated, ordered, fully hydrated evidence
5. Personality-aware grounded synthesis

Current implementation is in:

- `shrecknet/app/jobs/elder/elder.py` (stable v2 entrypoint)
- `shrecknet/app/jobs/elder/query_v2.py`
- `shrecknet/app/jobs/elder/schemas.py`
- `shrecknet/app/api/routers/elder.py`

## Runtime Flow

### 1. Query Construction

- Validates agent and ontology scope.
- Optionally loads chat memory from `chat_id` (recent messages).
- Builds a bounded retrieval plan according to route mode (`auto`, `fast`, `deep`).

Each intent contains:

- `subquery`
- `target_data_type` (`entity | scene | milestone | mixed`)
- `reason`
- `top_k_entities` (retrieved `EntityInstance` node IDs for that subquery)
- `top_k_scenes` (retrieved `Scene` node IDs for that subquery)
- `top_k_milestones` (retrieved `Milestone` node IDs for that subquery)

Type guidance used by decomposition:

- `entity`: who/what identity questions
- `scene`: what happened in context
- `milestone`: arc progression / when-how evolution
- `mixed`: broad multi-type question

### Route and Fast Behavior (Current)

`fast` and `route` are both accepted on `ElderQueryRequest`.

- `route=fast`: single mixed intent (`subquery = original query`).
- `route=deep`: plan first, then execute bounded retrieval steps.
- `route=auto`: fast-first pass, then expands to decomposition only if first pass is weak.

Backward compatibility rule in code:

- If `route` is omitted/`auto` and `fast=false`, the request is treated as `deep`.

After that, both modes follow the same downstream stages:

- candidate generation
- candidate consolidation
- reranking + memory priors
- grounded synthesis

Practical effect:

- fast mode reduces latency and token usage
- deep mode usually provides better coverage for multi-aspect questions

### 2. Candidate Generation

For each intent (parallel, bounded concurrency):

- Runs vector retrieval over the V2 `SemanticDocument` index (`semantic_document_vec_idx`).
- Applies label filtering from `target_data_type`.
- Uses retrieval windows:
  - `candidate_limit`
  - `rerank_limit`
- Returns node-backed chunks with scores and evidence fields.

### 3. Candidate Consolidation

- Groups results by `node_id`.
- Preserves node-level survival (no instance-level collapsing).
- Attaches top evidence chunks per node.

Output object is a `SourceNode` with:

- `node_id`
- `node_label`
- `node_name`
- `score`
- `evidence_chunks[]`

### 4. Reranking + Memory Priors

- Applies structured priors (not freeform query rewriting):
  - `entity_prior`
  - `temporal_prior`
  - `disambiguation_prior`
  - `continuity_prior`
- Records prior traces with:
  - `type`
  - `effect`
  - `targets`
  - `why`
  - `impact_on_scores`

### 5. Grounded Synthesis

- Synthesizes answer from source evidence only.
- Keeps answer aligned with retrieved nodes/chunks.
- Returns `answer` + `sources` for frontend provenance.

Legacy mode note:

- `mode=context` skips synthesis and returns empty `answer` with populated `sources`.

## Response Contract (Current)

`ElderQueryResponse` now returns:

- `agent_id`
- `query`
- `answer`
- `timings`
- `sources`
- `memory_priors_applied`
- `trace_id`
- optional `trace`
- optional `retrieval_debug`
- additive `pipeline_version` (`elder-query-retrieval-v2`)
- `llm_usage[]`, one row per Elder LLM call in execution order, with stage,
  model, input tokens, output tokens, and total tokens
- `llm_usage_totals`, containing aggregate call and token counts for the request

The same data is printed to service stdout as grep-friendly
`[ELDER_LLM_USAGE]` per-call lines and one `[ELDER_LLM_USAGE_TOTAL]` line,
correlated by `trace_id` and `agent_id`.

Requests may add `instance_id` to restrict retrieval to one ontology instance assigned to
the Elder. Omitting it preserves the existing all-assigned-ontology behavior.

## Latency and Observability

Elder logs and returns step timings:

- `decompose_ms`
- `memory_summary_ms`
- `retrieve_ms`
- `consolidate_ms`
- `rerank_ms`
- `synthesize_ms`
- `total_ms`

In fast mode, `decompose_ms` is minimal because no multi-intent decomposition is executed.

Per-intent logging includes:

- subquery
- target type
- duration
- top node ids
- retrieval counters (`raw_candidates`, `after_parent_grouping`, `after_dedup`, `final_k`)

## Chat Memory Relation

`chat_id` memory is used as a bounded bias layer in retrieval ranking.

- Memory is summarized from recent turns.
- Memory does not directly rewrite user intent text.
- Priors are explicit and traceable in response payload.

When `chat_id` is provided, router-level persistence stores:

- user message,
- assistant answer,
- assistant metadata (`sources`, `timings`, `memory_priors_applied`, `trace_id`, optional `trace`).

## Embedding/Reconciliation Relation

Elder depends on scene-centric embedding freshness.

- Backing memory nodes: `EntityInstance`, `Scene`, `Milestone`
- Retrieval vectors: `SemanticDocument.text_embedding`
- Load control: scene/milestone writes now trigger coalesced embedding reconciliation jobs instead of broad per-write full-ontology fanout.

## Operational Notes

### Local debug artifacts

`elder_debug_artifacts_enabled` defaults to `true`. Each Elder v2 run writes an ordered
artifact directory under `local_tests/elder/query_<UTC timestamp>/`, using the configured
data directory first and the repository database directory as fallback. The files capture
request grounding, exact planner prompt and raw response, validated plan, deterministic
retrieval, complete unified evidence, exact synthesis prompts and raw responses, final API
response, and a manifest. Artifact I/O is best-effort and never changes query execution.

- Elder requires OpenAI configuration for decomposition and synthesis.
- Retrieval works across all ontologies assigned to the target agent.
- Frontend should treat `sources` as grounding/provenance and can display labels for explainability.
