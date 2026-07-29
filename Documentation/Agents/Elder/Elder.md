# Elder Agent — Query and Retrieval V2

This document describes the sole supported Elder query and retrieval system.

## V2 Contract

The normal path uses independently configured `model_elder_planner`,
`model_elder_synthesis`, and `model_elder_character_incorporation` targets.
Planning detects the original query's BCP-47 language while selecting at most
five retrieval operations. After deterministic retrieval, neutral English
synthesis returns atomic factual claims with trusted evidence IDs. Character
incorporation receives only the original query, detected language, agent name,
description, writing style, and citation-free claim text.
The synthesis call receives full, atomic source records, including canonical node
properties, every semantic document for each accepted source, provenance, and temporal
metadata. Every terminal evidence step declares an `evidence_type`; the server maps
it to an immutable 12,000–100,000 token target. The source that crosses a step's
soft target is included in full, then collection for that step stops. Records are
never partially truncated.

If accepted records exceed the synthesis model context they are processed in
record-boundary batches and combined by an additional synthesis pass. A single record
too large for one model call produces a typed capacity failure instead of partial evidence.

## Jobs Overview

Elder is a retrieval-grounded question answering pipeline over scene-centric memory.

Main endpoint:

- `POST /jobs/elder/{agent_id}/query`

Chat stream-compatible endpoint:

- `POST /chat/messages/stream`

Model configuration uses the shared admin endpoints:

- `GET /config/` reads all three Elder model targets.
- `PUT /config/` updates either target.
- `GET /config/schema` exposes both fields in the Elder group.
- `GET /llm_status/` reports readiness for both targets.

Planner, synthesis, and character calls request strict JSON Schema output when
supported. Malformed planner plans, neutral synthesis payloads, and character
renderer payloads are repaired through the shared `model_agents_repair_json`
target. The stage-specific Elder model remains responsible for primary generation;
the global repair target is the single authority for JSON and schema correction.
Character output contains cohesive passages associated with claim IDs. It may
reorder, combine, and condense claims, but every claim ID must occur exactly
once and the prose cannot contain citation markup or source identifiers. The
backend restores trusted attribution as Unicode superscript numbers in source
order (`¹`, `²`, …, `¹⁰`). Each number refers to the corresponding one-based
entry in `sources[]`; full attribution remains available through
`sources[].evidence_id` and the complete structured source record. Invalid,
unavailable, or timed-out character rendering is retried once through
`model_agents_repair_json` with a corrective contract prompt.
If the configured model fails the contract twice, the Elder request fails
explicitly; it never presents neutral synthesis as a successful in-character
answer. When no character target is configured, neutral rendering remains the
configuration-level fallback.

## Goal

Given a user question, Elder returns a grounded answer plus explicit source nodes (`EntityInstance`, `Scene`, `Milestone`) used to build the response.

The Elder v2 architecture is:

1. Ontology, instance, entity, and conversation grounding
2. One validated retrieval plan with at most five operations
3. Parallel deterministic retrieval waves
4. Unified deduplicated, ordered, fully hydrated evidence
5. Neutral English atomic-claim synthesis with citation attribution
6. Cohesive language and character composition
7. Deterministic superscript rendering with structured source attribution

### Temporal planning and ordering

Each retrieval step can explicitly choose `temporal.ordering` (`relevance` or
`recency`), `temporal.direction` (`ascending` or `descending`), and its own
result `limit`. The planner normally chooses a limit near 10 for an unspecified
recent-history request, but this is guidance rather than a fixed window.

Recency compares `updated_at` first and `created_at` second. Records without
either timestamp are retained as non-comparable records after timestamped
results; Elder does not invent a temporal position for them. `FOLLOWED_BY` and
`PRECEDED_BY` are local source-order relationships and are not used to order
records across sources.

Temporal expansion preserves the planner-selected order through evidence
consolidation and synthesis. `created_at`, `updated_at`, source/scene identifiers,
the selected rank, and whether the record was temporally comparable are retained
in evidence metadata.

For non-temporal plans, evidence remains relevance-ranked. For temporal plans, the
planner-selected temporal rank takes precedence when the synthesis budget selects
which sources fit.

Current implementation is in:

- `shrecknet/app/jobs/elder/elder.py` (stable v2 entrypoint)
- `shrecknet/app/jobs/elder/query_v2.py`
- `shrecknet/app/jobs/elder/schemas.py`
- `shrecknet/app/api/routers/elder.py`

## Runtime Flow

### 1. Query Construction

- Validates agent and ontology scope.
- Optionally loads chat memory from `chat_id` (recent messages).
- Builds one adaptive bounded retrieval plan.

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

### Adaptive planning

Elder has one planner-driven execution path. The removed `fast` and `route`
request fields are not accepted. A deterministic resolved-entity overview
shortcut remains for narrow profile questions. It selects the single exact
query entity (confidence `>= 0.99`) and ignores lower-confidence fuzzy candidates.
If planner generation or validation fails for such a query, the fallback plan
also uses an exact profile lookup plus entity-bound narrative context rather than
an unconstrained search over the full conversational query.

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
