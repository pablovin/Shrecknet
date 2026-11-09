# Unreleased — Companion retrieval execution

- Reuse executable Atlas plans or deterministically execute a light question;
  reserve additional Elder planning for explicit deep requests without a plan.
  Filter exceptional read results against pinned ontology/instance scope.
- Default individual ShreckWorld questions to single evidence retrieval, retaining
  planned retrieval for complex research and bounded deep escalation.
- Require a personal-history/perspective reason for character-memory retrieval;
  retain identity, traits and rapport in every response.
- Recover permitted public information after local failure or reviewed evidence
  gaps without a second Web-selection vote. Share one bounded recovery batch,
  prepare safe public queries, preserve source categories and verify final claims.
- Default retrieval/model calls to 600 seconds, rounds to 615 and turns to 10,200.
  One-time startup configuration migrations raise shorter saved limits; preserve
  longer limits and later administrator edits.
  Add per-call timeout/depth diagnostics and fifteen-second lease heartbeats with
  ninety-second stale detection. Redis visibility outlives long turns.
- Bootstrap the shared gateway provider/hedge limits and ShreckWorld LLM deadline
  at 600 seconds. Upgrade shorter saved gateway limits, Companion hedge routes and
  the orphan watchdog once. Explicit ShreckWorld environment overrides remain.

No SQL schema migration, graph rewrite or re-embedding is required. The two
configuration databases receive atomic, version-marked timeout data migrations. Drain active work
and reload API/workers together. Shared gateway limits affect other workloads;
validate concurrency, proxies, authentication lifetime and provider capacity.
See the [canonical contract](../Documentation/Agents/Companion/Companion.md) and
[upgrade values](../Documentation/Agents/Companion/Model%20Configuration.md#ten-minute-call-rollout).

- Retain evidence after assessment exceptions. Retry with a bounded compact subset;
  resume normal verified synthesis when possible, otherwise publish only attributed
  extracts with explicit limitations and no inferred factual claims.
- Separate partial index readiness from retrieval success, preserve usable active
  generations during reindexing, and expose affected book IDs/titles/reasons.
- Compact model evidence/rich provenance while retaining full navigation metadata
  server-side. Audit omitted evidence and expose assessment timings, input size,
  exception/validation outcomes and retained-source counts in owner-only diagnostics
  and saved Answer Breakdown data. No SQL schema migration is required.

- Replace model-facing executable plans with search/chat research tasks. Compile
  IDs, permitted calls and light/deep strategies in the backend; separate scope,
  continuation, memory relevance and safe public-query decisions. Recover invalid
  planning with original-goal local search, permit nine initial calls and allocate
  retained evidence fairly across needs. Assessment proposes semantic follow-ups.
  Keep API payloads, saved answers, timeout migrations and evidence safeguards.
