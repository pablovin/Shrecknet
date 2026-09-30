# Elder Query V3 — Implementation and Compatibility Plan

## Status and decision

This is the proposed implementation target for the next Elder query revision. It
does not describe the runtime currently deployed by `ElderQueryV2`: that runtime
still paginates ontology entities during query grounding, normally calls the
planner, hydrates complete sources, and uses a separate character-rendering LLM
call. The current API contract remains documented in
[Elder](./Elder.md) and [Elder Query Contract](./Querry/Querry.MD).

The objective is a faster, cheaper, and more precise grounded answer path without
changing the graph model, SemanticDocument index, ontology/instance isolation, or
deterministic source attribution.

## Target runtime

For ordinary questions, the runtime is:

```text
request → cached grounding and alias resolution → deterministic route
        → compact deterministic retrieval → one grounded Elder synthesis
        → validation, citation rendering, response
```

Only a query that cannot be classified safely uses the planner:

```text
request → grounding → route = complex → one bounded retrieval plan
        → retrieval → one grounded Elder synthesis → response
```

Normal paths therefore make one LLM call. Complex paths make at most two, before
the exceptional single structured-output repair. The final synthesis produces both
grounded claims and Elder-voice passages; it replaces the mandatory
`character_incorporation` Elder stage.

## Required implementation work

| Area | Current behavior | Required change | Primary owner |
| --- | --- | --- | --- |
| Entity resolution | `query_v2.py` pages all entities and compares aliases in Python. | Add ontology- and instance-scoped normalized alias lookup. Prefer Neo4j exact lookup plus bounded full-text/fuzzy fallback; an invalidated process cache is an acceptable first step. Never scan all entities on the hot path. | `jobs/elder`, Neo4j retriever |
| Ontology grounding | The API loads definitions per request; `grounding.py` only caches the supplied definition payload by hash. | Compile/cache a minimal grounding catalogue keyed by a stable ontology revision, with explicit invalidation on ontology/entity changes. | Elder grounding owner, ontology write paths |
| Routing | A narrow entity-overview shortcut is the only deterministic route. | Add conservative Python routing for entity fact/overview/recent history/timeline, relationship, event lookup, latest record, and semantic fact. Fall through to the planner on ambiguity. | `jobs/elder/router.py` or `query_v2.py` |
| Conversation continuity | Regex-derived names and score boosts run after evidence is selected. | Build structured anchors from persisted assistant metadata before resolution/retrieval: entity, scene, milestone, and source IDs plus the last topic. Apply them to candidate selection, not only returned scores. | router, chat persistence |
| Retrieval breadth | Request defaults are 120 candidates and 50 reranked nodes. | Make server-owned ordinary defaults 40 and 12. Routes/plans may increase them only for deep/history/exhaustive work. | schemas, executor, router/planner |
| Hydration/evidence | `complete_source` is default and long text may appear in both text and facts. | Hydrate matched chunk plus one neighbor on each side, target roughly 800–1,500 tokens per source, and remove text-like properties duplicated in `display_text`. Complete source is explicit-only. | evidence, Neo4j retriever |
| Budgets | Per-terminal-step soft budgets are 12k–100k tokens. | Apply response-scope budgets to final selected evidence: brief 1.5–3k, standard 5–8k, deep 15–30k. Keep a complete selected record when it only slightly crosses a soft limit. | v2 schemas, evidence |
| Planning | Planner receives broad grounding and is mandatory outside one shortcut. | Use it only for `complex`, with compact relevant vocabulary, resolved aliases, anchors, and 1–5 bounded operations. A malformed planner falls back to bounded hybrid retrieval. | planner, prompts |
| Final generation | Neutral synthesis plus character rendering; overflow is sequential. | Add a strict single-call final schema with claims, rendered passages, and uncertainty. Enforce generation limits (planner 400–600; brief 400; standard 800; deep 1,600; repair 600). Bound/parallelize any remaining overflow work. | final synthesis, prompts, LLM client |
| Validation | Citation validation exists for neutral claims. | Validate unique claim IDs, supplied citations only, valid passage claim references, every claim rendered exactly once, no evidence IDs in prose, and visible uncertainty. Restore markers server-side. | final synthesis |
| Configuration | Three Elder model targets include a character-incorporation model. | Keep the current setting during migration, stop reading it on the V3 Elder path, then remove it from config schemas/defaults/admin UI only after confirming it has no non-Elder callers. Add server-owned output-limit settings only if they need operational tuning; do not expose them to chat clients. | config store, configuration router, deployment docs |
| Operations | Timings and LLM usage exist but do not identify routing separately. | Retain `grounding_ms`, `retrieve_ms`, `consolidate_ms`, `synthesize_ms`, and `total_ms`; add `route_ms`; emit `plan_ms` only when the planner ran. Keep per-call model, token, and wait metrics. | orchestration, observability |

## Public API and frontend contract

The two endpoint paths remain unchanged:

- `POST /jobs/elder/{agent_id}/query`
- `POST /chat/messages/stream`

The following response fields remain stable: `agent_id`, `query`, `answer`,
`timings`, `retrieval_plan`, `sources`, `trace_id`, optional `trace`,
`llm_usage`, and `llm_usage_totals`. Compact hydration changes the *amount* of
source text, not the `sources[]` shape. Citation markers remain display-ready in
`answer` and map to the one-based `sources[]` order.

No frontend change is required for a client that only sends `query`, optional
`chat_id`/`instance_id`, and renders `answer` plus `sources`. Frontends must not
assume a planner call occurred, a fixed list of timing keys, a particular
`retrieval_plan.steps` count, complete source text, or a particular
`pipeline_version` string.

### Additive changes recommended for the implementation

Add this optional public request field so scope is user-selectable rather than an
opaque planner decision:

```json
{ "response_scope": "brief" }
```

Allowed values are `brief`, `standard`, and `deep`; omitted means `standard`.
`exhaustive` is deliberately not a normal scope and requires explicit server-side
intent handling. Add the same field to the Python SDK request model and examples.

Add `route` as an optional additive field inside `retrieval_plan` (or an
equivalent documented top-level field) with values such as `entity_overview`,
`relationship`, and `complex`. Clients may display it in diagnostics but must
treat unknown values as informational. `timings.route_ms` and conditional
`timings.plan_ms` are additive dictionary keys.

### Fields to deprecate safely

`candidate_limit` and `rerank_limit` expose infrastructure controls that conflict
with route-owned retrieval breadth. Keep them accepted during a compatibility
window, clamp them server-side, document them as deprecated, and remove frontend
use before a major-version removal. Retain legacy `mode` during that same window;
do not let `mode=context` bypass the new grounded-answer contract without an
explicit compatible replacement.

The Python SDK currently does **not** expose the backend's existing `instance_id`
request field. Updating that SDK model is a separate contract-alignment change
needed before clients can scope Elder queries through the SDK.

## Data, cache, and invalidation requirements

- Alias records need normalized alias, node ID, ontology ID, instance ID, and
  entity-definition ID. Exact lookup must enforce ontology and optional instance
  scope before returning a result.
- Define the invalidation producer for entity alias edits, entity delete/create,
  ontology relationship/property-definition edits, ontology assignment changes,
  and instance lifecycle changes. A cache without these hooks is not acceptable
  beyond a short TTL safety net.
- Persist compact structured conversation anchors in assistant message metadata.
  Existing `sources` metadata is useful input, but it must be normalized into
  explicit IDs before retrieval and must remain ownership-scoped by `chat_id`.
- Continue to exclude non-canonical graph labels and preserve instance isolation
  in every alias, retrieval, hydration, and fallback query.

## Prompt and schema changes

The final synthesis input contains the query, language, response scope, Elder
identity/style, limited recent conversation text, and compact evidence. It must
return:

```json
{
  "claims": [{"id": "claim-1", "text": "…", "citations": ["evidence-1"]}],
  "rendered_passages": [{"text": "…", "claim_ids": ["claim-1"]}],
  "uncertainty": null
}
```

The prompt, Pydantic schema, validator, debug artifacts, and tests must change
together. Provider-native JSON Schema is the primary path; at most one repair
attempt is allowed and it receives the original evidence-bearing context.

## Delivery order and acceptance gates

1. Establish a baseline from at least 20 representative questions. Save traces,
   sources, timing, token usage, LLM-call count, answer specificity, correctness,
   and citation correctness.
2. Add cache invalidation, alias lookup, lower server defaults, compact hydration,
   duplicate-text removal, and scope budgets. Benchmark before continuing.
3. Add router tests and deploy deterministic routes behind an observable feature
   flag. The planner is the fallback for `complex` only.
4. Replace the two synthesis/render calls with final synthesis plus deterministic
   validation. Preserve response shape and citation behavior.
5. Move structured conversation anchors before evidence selection, then simplify
   overflow/concurrency behavior and remove dead character-rendering code.
6. Remove compatibility paths only after consumers and the SDK have migrated.

Success criteria for ordinary questions: planner use below 25%, one LLM call,
usually fewer than 8k evidence tokens and 2–8 sources, answer under 500 output
tokens, and improved p50 and p95 latency without a correctness or citation
regression.

## Required tests

- Router classification for every supported route and conservative `complex`
  fallback.
- Alias exact/case-insensitive/ambiguous behavior, ontology and instance
  isolation, and invalidation.
- Two-turn continuity that proves anchors constrain retrieval before synthesis.
- Local versus complete hydration, duplicate long-property removal, scope budgets,
  pre-selection memory priors, and temporal ordering.
- One-call normal synthesis, bounded output, repair cap, citation/passage
  validation, narrow answers, and grounded retrieval-failure behavior.
- Endpoint/SDK compatibility, authorization and chat ownership, exclusion rules,
  and benchmark regression coverage.

## Related documentation

- [Current Elder runtime and stable response contract](./Elder.md)
- [Detailed Elder request, response, and frontend contract](./Querry/Querry.MD)
- [Retrieval endpoint reference](../../SceneCentricMemory/Retrieval/Retrieval%20-%20Endpoints.md)
- [Python SDK Elder lifecycle](../../../python_sdk/docs/elder/query-lifecycle.md)
