# Elder Agent — Current Query and Retrieval Contract

Elder is Shrecknet's retrieval-grounded knowledge agent for canonical `EntityInstance`, `Scene`, and `Milestone` memory. It returns display-ready answers with deterministic, structured source attribution.

This page documents the runtime currently in the repository. The proposed next revision is the [Elder Query V3 implementation plan](./ELDER_QUERY_V3_IMPLEMENTATION_PLAN.md).

## Endpoints

- `POST /jobs/elder/{agent_id}/query`
- `POST /chat/messages/stream` (stream-compatible request endpoint; response is `ElderQueryResponse`)

Both require normal authentication and enforce Elder ownership, assigned ontology scope, optional instance scope, and chat ownership. The old `/jobs/elder/chat/messages/stream` path is not registered.

## Current runtime

`ElderQueryV2` is the stable orchestrator name. Its public pipeline version is `elder-query-retrieval-v3`.

1. The API validates the agent, optional `instance_id`, and optional `chat_id`, then loads recent chat history and ontology definitions.
2. Query grounding resolves aliases by paginating entities in every assigned ontology and applying Python similarity matching. `grounding.py` caches a supplied definitions payload process-locally, but the API still loads definitions for each request.
3. A narrow exact-entity overview builds a deterministic plan. Other queries call the retrieval planner.
4. The executor performs bounded deterministic retrieval waves and applies ontology/instance filtering.
5. Evidence is consolidated by node and hydrated using `complete_source` mode. Terminal planner evidence types use 12k–100k soft token targets.
6. Existing memory priors are calculated after evidence assembly and adjust source scores; they do not yet constrain retrieval selection.
7. Neutral structured synthesis produces cited atomic claims. A separate Elder character-incorporation call turns those claims into answer prose. The backend renders trusted superscript source markers.

When evidence exceeds model capacity, current synthesis batches complete records, creates memoranda, runs an overflow final synthesis, and still runs character rendering. A source that cannot fit a model call produces the typed `elder_evidence_capacity_exceeded` HTTP 413 response.

## Request contract

`ElderQueryRequest` accepts:

- `query` — required non-empty user question.
- `mode` — legacy `nl | context | both`, default `both`. `context` returns an empty answer after retrieval.
- `include_trace` — includes internal trace data when true.
- `chat_id` — optional owned Elder chat for recent conversational context.
- `instance_id` — optional assigned ontology-instance restriction.
- `node_scope` — legacy retrieval preference, default `everything`.
- `candidate_limit` — optional 5–200 candidate chunk cap, default 120.
- `rerank_limit` — optional 1–100 reranked node cap, default 50.

`entities_hint` and `grounding_definitions` are internal/API-layer fields and are not frontend controls. Do not send undocumented evidence-budget fields: the current evidence budget is owned by the server's planner evidence type.

## Response contract

`ElderQueryResponse` returns:

- `agent_id`, `query`, and display-ready `answer`
- `timings` with `grounding_ms`, `plan_ms`, `retrieve_ms`, `consolidate_ms`, `rerank_ms`, `synthesize_ms`, and `total_ms`
- `retrieval_plan`, including answer goal, selected scope, and public projection of retrieval steps
- `sources[]`, the evidence supplied to synthesis, ordered so superscripts in `answer` refer to one-based source positions
- `memory_priors_applied`, `trace_id`, optional `trace`, and `retrieval_debug`
- `pipeline_version` (`elder-query-retrieval-v3`)
- `llm_usage[]` and aggregate `llm_usage_totals`

Each `sources[]` item includes its node identity/display data, score, evidence ID, chunks, provenance, temporal position, retrieval methods, canonical text, and safe properties. These are provenance records, not a promise that the full canonical source will always be returned to a client.

The server writes the same response metadata into assistant chat messages. That metadata currently contains `sources`, timings, plan, priors, trace ID, version, and LLM usage; it is the migration source for V3 structured continuity anchors.

## Observability

Elder reports the timings above and emits `[ELDER_LLM_USAGE]` lines with call stage, model, input/output/total tokens, and wait time, followed by one `[ELDER_LLM_USAGE_TOTAL]` line keyed by trace ID and agent ID. Debug artifacts, when `elder_debug_artifacts_enabled` is enabled, are best-effort local files and never alter query execution.

## Client guidance

Render `answer` as the answer and `sources[]` as provenance. Treat timing keys, retrieval-plan operations, evidence chunk length, and pipeline version as evolvable diagnostics. See [Elder Query Contract](./Querry/Querry.MD) for frontend details and the [V3 plan](./ELDER_QUERY_V3_IMPLEMENTATION_PLAN.md) for additive `response_scope` and migration guidance.

## Code ownership

- `shrecknet/app/api/routers/elder.py` — HTTP validation, authorization, chat persistence
- `shrecknet/app/jobs/elder/query_v2.py` — orchestration
- `shrecknet/app/jobs/elder/grounding.py`, `planner.py`, `executor.py`, and `evidence.py` — pipeline stages
- `shrecknet/app/jobs/elder/schemas.py` and `v2_schemas.py` — public and internal contracts
