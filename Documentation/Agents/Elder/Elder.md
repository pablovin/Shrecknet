# Elder — Site-wide Query and Retrieval Contract

Elder is Shrecknet's retrieval-grounded knowledge assistant for canonical `EntityInstance`, `Scene`, and `Milestone` memory. It returns display-ready answers with deterministic, structured source attribution.

This page documents the runtime currently in the repository. The proposed next revision is the [Elder Query V3 implementation plan](./ELDER_QUERY_V3_IMPLEMENTATION_PLAN.md).

## Built-in Elder and caller-selected worlds

Elder has one built-in website identity. Users and calling agents select its worlds per conversation/request; administrators do not assign maximum worlds or configure Elder identities. Existing `enable_ai_agents` and the three `model_elder_*` provider/model targets remain the configuration controls. No hidden Agent is created.

- `GET /jobs/elder/status` returns the authenticated viewer-safe identity and activation state.
- `POST /jobs/elder/chats/` accepts `name`, optional `color`, and `world_scope` without `agent_id`.
- `POST /jobs/elder/query` accepts an owned `chat_id`, or an explicit `world_scope` for stateless callers.
- `POST /chat/messages/stream` also accepts agentless scoped requests; its JSON completion contract is unchanged.
- Chat list/read/update/delete routes remain at `/jobs/elder/chats/`; lists without an Agent filter include the current user's new and historical chats.

`world_scope` is `{ "mode": "selected", "ontology_ids": [7] }` for one world,
`{ "mode": "selected", "ontology_ids": [7, 8] }` for several, or `{ "mode": "all" }`.
World IDs must be positive, existing, and readable under the platform's current
world-access rules. At present, signed-in world reading has a shared audience;
private perspectives retain their existing retrieval rules. The backend resolves
scope before catalogue grounding, matching, graph/vector retrieval, and synthesis.
Caller-provided grounding definitions or entity hints never replace that scope.
Optional page focus is validated through canonical graph services against it.

New chats store a fixed, resolved world snapshot and have `agent_id: null`,
`scope_mode`, `ontology_ids`, and `read_only: false`. An all-world chat includes
worlds available at its creation; create another chat to include later additions.
An explicit query scope conflicting with that snapshot returns 409. Stateless
queries require a scope (422 if absent); unknown worlds return 404, unavailable AI
returns 503, and missing/non-owned chats return 404. A removed world makes the
scoped chat unavailable rather than silently narrowing its historical context.
Long-running global queries revalidate their worlds before saving the reply.

Sources include `ontology_id` when known, allowing citations to navigate to their
own world. A single-world fallback supports older source metadata. New context
uses `ElderExecutionContext`, not a persisted Agent. The companion orchestrator
allocates that built-in tool with its chosen world, without discovering an Elder
Agent assignment.

## Conversation compatibility, quota, and migration

Ten new site-Elder chats are allowed per user across all scopes. Historical chats
remain separate and do not count toward that quota. They are readable with
`read_only: true` in the new interface; the global query rejects their ambiguous
historical context with 409. Older Agent-scoped query/create APIs remain
compatibility paths and retain their assigned-world scope. They verify that an
owned chat belongs to the specified Agent. No chats or Agent records are
implicitly merged or deleted.

Back up application SQLite and `media/chats` together before deploying Shrecknet.
Startup adds nullable scope and legacy-storage provenance, and rebuilds the old
required/cascading Elder Agent foreign key into a nullable `ON DELETE SET NULL`
reference. It preserves rows, indexes, and dependent SQL history, disabling
SQLite foreign keys only outside the table-copy transaction and restoring their
previous setting. This migration is idempotent. Legacy transcript paths remain
resolvable even if their old Agent is later deleted. New files use
`media/chats/{user_id}/site-elder/{chat_id}.json`; legacy files retain their
original Agent directories. SQL-only legacy history remains readable when a
transcript file does not exist. Context trimming never mutates stored history.

Deploy backend and consumers before retiring old setup. Both frontends remove
Elder from Agent Studio; guided installation no longer creates an Elder Agent.
World embedding/index state remains reusable. Administrator maintenance uses
`DELETE /jobs/worlds/{ontology_id}/embedding-jobs[/{job_id}]`, restricted to that
world's Neo4j embedding records. These retain the existing record-removal
semantics; deleting a job record does not guarantee that an executing worker has
stopped.

An older required-Agent schema cannot represent newly created chats. Rollback
requires compatible readers or exporting new chats before restoring the combined
SQL/file backup. Drain in-flight work before reverting and do not delete retained
Agent directories during rollout. Real provider/index execution and many-world
performance require operational validation.

## Legacy query endpoints

- `POST /jobs/elder/{agent_id}/query`
- `POST /chat/messages/stream` (stream-compatible request endpoint; response is `ElderQueryResponse`)

These compatibility paths require normal authentication, enforce assigned ontology scope and chat ownership, and retain optional instance scope. The stream adapter can also dispatch the new agentless scoped contract. The old `/jobs/elder/chat/messages/stream` path is not registered.

## Current runtime

`ElderQueryV2` is the stable orchestrator name. Its public pipeline version is `elder-query-retrieval-v3`.

1. The API resolves the caller-selected world snapshot (or a compatibility Agent), validates optional `instance_id` and owned `chat_id`, then loads recent history and scoped ontology definitions.
2. Query grounding resolves aliases by paginating entities in every resolved ontology and applying Python similarity matching. `grounding.py` caches a supplied definitions payload process-locally, but the API still loads definitions for each request.
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

## Completion and connection recovery

Both query entry points return one JSON response after the entire pipeline and
owned-chat persistence complete. `/chat/messages/stream` is a compatibility path;
it does not send SSE events or progress heartbeats. A lost HTTP connection does
not prove that generation failed or stopped. Completed turns can already be
saved and become visible on the next history read.

Clients should display the returned `answer` and `sources` immediately rather
than depending on a second chat-list or history request. On an ambiguous
transport failure, read the exact submitted `chat_id` through
`GET /jobs/elder/chats/{chat_id}?include_history=true`. Compare with the
pre-submission transcript and require a new matching user question followed by
an assistant reply; an older identical question is not evidence of completion.
Do not automatically repeat the query POST. History access still requires the
owning user's authentication; no new public polling route is introduced.

The Foundry and ShreckOS Atlas clients recover through bounded history reads.
ShreckOS uses a dedicated 15-minute query deadline and its bundled Nginx proxy
allows 900 seconds on Elder query paths; ordinary requests keep their shorter
deadlines. External proxies, including the Foundry browser's Shrecknet API
proxy, need a compatible timeout and correct CORS headers on error responses.
This client/deployment change does not alter `ElderQueryResponse`, chat schemas,
SDK contracts, or persistence. See the [Foundry operations guide](../../Deployment/FOUNDRY_MODULE.md)
for plugin recovery behavior.

## Observability

Elder reports the timings above and emits `[ELDER_LLM_USAGE]` lines with call stage, model, input/output/total tokens, and wait time, followed by one `[ELDER_LLM_USAGE_TOTAL]` line keyed by trace ID and agent ID. Debug artifacts, when `elder_debug_artifacts_enabled` is enabled, are best-effort local files and never alter query execution.

## Client guidance

Render `answer` as the answer and `sources[]` as provenance. Treat timing keys, retrieval-plan operations, evidence chunk length, and pipeline version as evolvable diagnostics. See [Elder Query Contract](./Querry/Querry.MD) for frontend details and the [V3 plan](./ELDER_QUERY_V3_IMPLEMENTATION_PLAN.md) for additive `response_scope` and migration guidance.

## Code ownership

- `shrecknet/app/api/routers/elder.py` — HTTP validation, authorization, chat persistence
- `shrecknet/app/jobs/elder/query_v2.py` — orchestration
- `shrecknet/app/jobs/elder/grounding.py`, `planner.py`, `executor.py`, and `evidence.py` — pipeline stages
- `shrecknet/app/jobs/elder/schemas.py` and `v2_schemas.py` — public and internal contracts

## Retrieval reuse

`app/services/elder_retrieval_service.py` exposes the established deterministic
`ElderRetrievalExecutor`. Elder and Character Companion call this same owner;
Companion supplies a bounded hybrid-search plan and does not run Elder synthesis.
See [Character Companion](../Companion/Companion.md) for independent tool deadlines,
normalized evidence and final citation validation.
