# Elder Agent

This document describes the Elder query system in production scope.

## Jobs Overview

Elder is a retrieval-grounded question answering pipeline over scene-centric memory.

Main endpoint:

- `POST /jobs/elder/{agent_id}/query`

Chat stream-compatible endpoint:

- `POST /chat/messages/stream`

## Goal

Given a user question, Elder returns a grounded answer plus explicit source nodes (`EntityInstance`, `Scene`, `Milestone`) used to build the response.

The current Elder architecture is layered:

1. Query construction
2. Candidate generation
3. Candidate consolidation
4. Reranking (with structured memory priors)
5. Grounded synthesis

Current implementation is in:

- `shrecknet/app/jobs/elder/elder.py`
- `shrecknet/app/jobs/elder/schemas.py`
- `shrecknet/app/api/routers/elder.py`

## Runtime Flow

### 1. Query Construction

- Validates agent and ontology scope.
- Optionally loads chat memory from `chat_id` (recent messages).
- Builds intents according to route mode (`auto`, `fast`, `deep`).

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
- `route=deep`: decompose first, then retrieve (bounded to top 3 intents).
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

- Runs vector retrieval over `EntityChunk` index (`entity_chunk_vec_idx`).
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
- `intents`
- `sources`
- `memory_priors_applied`
- `trace_id`
- optional `trace`
- optional `retrieval_debug`

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
- Retrieval vectors: `EntityChunk.text_embedding`
- Load control: scene/milestone writes now trigger coalesced embedding reconciliation jobs instead of broad per-write full-ontology fanout.

## Operational Notes

- Elder requires OpenAI configuration for decomposition and synthesis.
- Retrieval works across all ontologies assigned to the target agent.
- Frontend should treat `sources` as grounding/provenance and can display labels for explainability.
