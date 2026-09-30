# Elder Query Contract

This document is the frontend-facing contract for the current Elder query API. The directory's historical `Querry` spelling is retained for link compatibility. For the intended next runtime, see the [V3 implementation plan](../ELDER_QUERY_V3_IMPLEMENTATION_PLAN.md).

## Endpoints

- `POST /jobs/elder/{agent_id}/query`
- `POST /chat/messages/stream` with `agent_id` in the body

Both return `ElderQueryResponse`; the latter is stream-compatible by route name, not a server-sent-event response.

## Current request fields

```json
{
  "query": "What happened to Ernst lately?",
  "mode": "both",
  "include_trace": false,
  "chat_id": "optional-chat-id",
  "instance_id": "optional-instance-id",
  "node_scope": "everything",
  "candidate_limit": 120,
  "rerank_limit": 50
}
```

`query` is required. `mode` accepts `nl`, `context`, or `both` and is legacy; `context` returns an empty `answer`. `instance_id` restricts retrieval to an assigned ontology instance. `candidate_limit` (5–200) and `rerank_limit` (1–100) are current optional controls, but clients should not rely on them as tuning guarantees because V3 will make ordinary retrieval breadth server-owned.

`entities_hint`, `grounding_definitions`, and evidence-budget controls are not public frontend inputs. In particular, do not send `synthesis_evidence_budget_tokens`: it is not part of `ElderQueryRequest`.

## Current response fields

```json
{
  "agent_id": "elder_01",
  "query": "What happened to Ernst lately?",
  "answer": "…¹",
  "timings": {
    "grounding_ms": 34.2,
    "plan_ms": 210.6,
    "retrieve_ms": 148.8,
    "consolidate_ms": 23.1,
    "rerank_ms": 1.2,
    "synthesize_ms": 402.4,
    "total_ms": 820.3
  },
  "retrieval_plan": {"answer_goal": "…", "response_scope": "standard", "query_intent": {}, "steps": []},
  "sources": [],
  "memory_priors_applied": [],
  "trace_id": "uuid",
  "trace": null,
  "retrieval_debug": [],
  "pipeline_version": "elder-query-retrieval-v3",
  "llm_usage": [],
  "llm_usage_totals": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
}
```

`answer` is display-ready Elder prose. Unicode superscripts refer to the one-based order of `sources[]`. Render sources as provenance; do not expose internal `evidence_id` values as user-facing text.

Every source has `node_id`, optional label/name/type fields, `score`, `evidence_chunks[]`, optional `evidence_id`, `provenance`, `temporal_position`, `retrieval_methods`, optional `canonical_text`, and safe `properties`.

`retrieval_plan` is a public projection, not executable client input. Its steps may contain `id`, `purpose`, operation/query/input/entity references, temporal and traversal information, target type, limit, and evidence target metadata.

## Frontend compatibility for V3

No frontend request/response migration is required to benefit from V3 if the client already submits `query` and renders `answer`/`sources`. The following additive changes are recommended:

- Support optional `response_scope: "brief" | "standard" | "deep"` when users need explicit answer-length control; omission remains `standard`.
- Treat `timings` as an extensible object. V3 adds `route_ms` and only emits `plan_ms` when a complex planner ran.
- Treat any route indicator and new `pipeline_version` as diagnostic data; never branch core UX on a known fixed step count or stage name.
- Stop sending `candidate_limit`/`rerank_limit` before their eventual removal.
- Do not assume that a source contains the complete canonical record; V3 returns local compact evidence by default.

The Python SDK needs matching `response_scope` support when V3 ships. It also needs an earlier alignment fix to expose the existing `instance_id` request field.

## Debugging

Use `trace_id` to correlate a client issue with server logs. Request `trace` only for trusted diagnostics. `llm_usage` is observability data and will vary between deterministic and complex routes.
