# Companion retrieval execution plan

Status: implemented design record; deployment smoke tests, provider behavior and
load/grounding benchmarks remain rollout acceptance gates. The runtime contract is
[Companion](./Companion.md#evidence-first-query-pipeline), and effective timeout
configuration is documented in [model configuration](./Model%20Configuration.md#ten-minute-call-rollout).
This record refines the earlier [query pipeline redesign](./Query%20Pipeline%20Plan.md).
The baseline observations below describe the code before this delivery; proposed
field names were implemented as specified unless noted in the delivery notes.

## Delivery notes

- Atlas reuses supplied Elder plans or a deterministic light plan. Only explicit
  deep calls without a reusable plan invoke Elder planning.
- ShreckWorld uses existing single/planned evidence strategies by call depth.
- Memory calls require relevance reasons; identity and traits remain independent.
- Backend Web recovery and reviewer follow-ups share one batch. Operational
  recovery precedes review; evidence-gap recovery is followed by reassessment.
- Missing public recovery queries use the bounded `PUBLIC_RECOVERY` renderer;
  unsafe/private-only queries remain blocked. No second source-selection vote runs.
- Per-call defaults are 600 seconds, round defaults 615, total turn defaults
  10,200. Timeout updates validate the conservative serial path. One-time startup
  configuration migrations raise shorter saved limits; longer limits and later
  administrator edits are retained.
- Outbox heartbeats run every fifteen seconds and stale detection uses ninety
  seconds, rather than the original turn-timeout-plus-thirty rule. Broker
  visibility outlives the admitted turn. Configuration-data migrations run on
  startup; no SQL schema or graph migration is required.

## Goals and current implementation

Plan once at the Companion level, retrieve evidence at the depth each need
requires, and recover public information when local retrieval fails or does not
answer the question. Keep character voice independent of personal-memory search.
Allow each retrieval or required model operation up to 600 seconds.

The inspected implementation has these relevant properties:

- `app/jobs/companion/query_contracts.py` defines `QueryPlan`, `Need`, and
  `PlannedCall`. A call currently carries only `id`, `tool`, `query`, and `need_ids`;
  it is not an executable Elder plan.
- `app/integrations/companion/retrieval.py::CompanionRetrieval.atlas` invokes
  `create_retrieval_plan` before the shared `retrieve_plan` executor.
- `CompanionRetrieval.shreckworld` always sends `mode="evidence"` with
  `retrieval_strategy="planned"`. ShreckWorld already supports `single` evidence
  retrieval, including through Companion grants.
- `app/jobs/companion/runner.py::load_identity` uses `include_memories=False`.
  Personal memory is already a planner-selectable tool; the missing piece is an
  explicit relevance contract, rather than removal of an unconditional fetch.
- `query_pipeline.py` recovers only through `Assessment.follow_up_calls`. It has
  no backend rule ensuring a web attempt for missing evidence.
- Code defaults are four seconds per local tool, twelve for web, twenty for a
  retrieval round, and 180 for a turn. Several HTTP clients also retain HTTPX's
  shorter default timeout. These are separate constraints.

Paths beginning `app/` above refer to `shrecknet/app/`.

## Proposed execution flow and contracts

1. Resolve authorized scope and load identity, effective traits, rapport, and
   bounded completed conversation using the existing owners.
2. Run the Companion planner once, except for the existing authorized world-switch
   replan. Return needs and typed source calls, including retrieval depth and
   public recovery queries where relevant.
3. Validate the plan and execute independent calls concurrently. Atlas executes a
   supplied validated plan or a deterministic conversion. ShreckWorld uses light
   evidence retrieval by default for an individual need.
4. Preserve successful evidence and normalize each source outcome. Schedule web
   recovery for local failure without waiting for a model to select Web again.
   Assess relevance and coverage with the existing batched evidence reviewer;
   unmet needs also trigger web recovery under the same policy.
5. Share one bounded recovery round across automatic web calls, deep escalation,
   and reviewer-proposed follow-ups. Reassess once after recovery.
6. Synthesize privately with identity and traits, verify factual claims, revalidate
   sources and access, then commit the final reply. Preserve durable progress and
   commentary throughout.

Extend `PlannedCall` additively with proposed optional fields:

| Field | Proposed contract |
| --- | --- |
| `retrieval_depth` | `light` or `deep`; default `light`; meaningful for Atlas/ShreckWorld |
| `depth_reason` | Null for light calls; nonempty bounded reason for deep calls |
| `atlas_plan` | Null, or the existing Elder `RetrievalPlan`; Atlas only |
| `memory_reason` | Null for other tools; required bounded explanation connecting a memory call to the selected character's experience or perspective |

Extend `Need` with `public_recovery_query`: null or a nonempty public query of at
most 500 characters. Null means that a safe public formulation has not been
established, not that Web is forbidden. All names here are proposed internal
contracts, not existing API fields. Preserve existing call signatures for legacy
callers through optional arguments or an explicit compatibility wrapper.

The planner and reviewer cannot supply credentials, service URLs, authority, or
new source scope. The backend owns permissions, ontology IDs, selected books,
character IDs, time budgets, and execution limits. Validate optional fields by
tool, ID uniqueness, need references, payload size and plan complexity. Keep
existing coarse public status enums; add versioned diagnostic detail as needed.

Update the full prompt JSON contracts, schema validation, source-code headers,
prompt definition order, and module/canonical flow descriptions together. Recovery
query rendering, when necessary, belongs beside the normal planning prompt.

## Atlas: reuse the Companion plan

Keep the existing Elder `RetrievalPlan`, validators and `retrieve_plan` executor
as the authority. Do not introduce a second graph execution language.

- When the Companion supplies `atlas_plan`, validate it using Elder's schema and
  scope validation before execution. Reject attempts to exceed resolved scope.
- For a light call without a detailed plan, construct a bounded `hybrid_search`
  step from the already-planned source query and associated need. Include the
  required terminal `evidence_type`, using the shared Elder contract. Prefer an
  existing suitable deterministic helper after verifying its semantics; the
  current `fallback_plan` can expand to several steps and must not be assumed
  equivalent to a single light search.
- A deep call may carry multiple dependent Elder steps for relationships,
  chronology, comparison, or graph structure. An explicit deep request without a
  reusable plan may invoke the existing Elder planner once; record this exception.
- Invalid supplied plans must not silently trigger another planner or widen
  access. Record validation failure and use the bounded recovery policy.

Put any reusable deterministic conversion beside the Elder plan owner; keep
Companion-specific selection in its job. Preserve provenance, revision checks,
ranking, rich-property projection, and absence of Elder public synthesis.

Acceptance: ordinary preplanned Atlas calls make zero
`companion.elder_planning` model calls, and return evidence accepted by the shared
executor and existing Companion verifier.

## ShreckWorld: light evidence with explicit depth

For a Companion-planned individual question, send the existing
`retrieval_strategy="single"`, `mode="evidence"`, scoped query and selected
`library_item_ids` to `/ontologies/{ontology_id}/companion-evidence` or the existing
legacy collection equivalent. Reuse ShreckWorld's current scoped candidate search,
ranking and citation hydration. No book-level LLM planning, child coverage review,
or answer synthesis runs on this path. Companion remains the coverage owner.

Retain `retrieval_strategy="planned"` for explicit deep calls: exhaustive research,
cross-book comparison, multiple interacting rules, or unresolved exceptions that
require decomposition. The planner must give a depth reason. A light result that
is partial or irrelevant may escalate once through the shared recovery round;
deep mode is not an unconditional second pass. Web recovery and deeper local
retrieval can run concurrently when their evidence is useful for different gaps.

Preserve `source_coverage`, source revisions, page references, index readiness,
edition constraints and requested source subsets on both paths. Preserve planned
mode's `query_plan`, `evidence_coverage`, `evidence_groups`, and `v3_trace`; do not
fabricate those structures for single retrieval. Similarity does not prove support.
Never report an unavailable index as evidence that a rule does not exist.
No new ShreckWorld strategy enum or API endpoint is required for the first delivery.

## Character memory: relevance without losing voice

Identity, psychological/personality summaries, effective traits and rapport remain
available for every answer. They shape expression without requiring memory search.

Plan `character_memory` only when a need concerns the selected character's past,
participation, relationships, remembered events, learned beliefs, or personal
perspective that depends on those experiences. A request for an opinion can need
memories; merely addressing the Companion or asking it to explain a rule does not.
Style preferences and hypothetical reactions can usually use traits alone.

Require `memory_reason` and validate its presence and associated need references.
Use the planner for semantic relevance; do not replace it with English keyword
matching. Recovery must follow the same rule. Retain selected-character isolation,
non-admin visibility filtering, and `subjective_memory` categorization. A memory
does not establish objective canon or override a rulebook.

Examples: “How does this spell work?” performs no personal-memory search.
“What happened when you first used this spell?” may retrieve that character's
memories alongside any requested rules evidence. An empty memory result cannot
license an invented biography.

## Explicit web recovery policy

Web recovery is backend orchestration, not another planner source-selection vote.
Use the existing configured Web availability/policy as the permission authority.
In current v2, `tools.web` is a legacy ignored field and configured Web is offered
even outside `tools.allowed`; do not silently reinterpret those fields as new
per-turn permission switches. Administrative disablement or missing capability
still prevents execution. A future permission-contract change needs its own
coordinated API/SDK/client change.

| Trigger for an unresolved need | Required action when Web is available and permitted |
| --- | --- |
| Relevant local call is unavailable, failed, or timed out | Enqueue a safe public recovery query without waiting for reviewer tool selection |
| Local call returns no evidence | Enqueue recovery unless existing evidence already supports the need |
| Reviewer marks evidence missing, partial, irrelevant, or non-answering | Enqueue recovery even if `follow_up_calls` omits Web |
| Contradiction remains | Search public sources when they can inform the disputed need; retain the contradiction |
| Review fails or is malformed | Keep evidence unverified; operational failure can still trigger recovery, but Web cannot bypass failed verification |

Use `public_recovery_query` prepared in the initial plan. If absent, a bounded
query-rendering operation may turn the unmet need into a public query; this
operation cannot decide whether recovery runs. Never forward raw private history,
Foundry snapshots, memory passages, or the failed local query automatically.
If no safe public formulation exists, record `privacy_blocked` and clarify.

Deduplicate by need, normalized public query, source and recovery round. Reuse a
completed relevant web attempt; do not repeat an equivalent search. Limit all
automatic/reviewer recovery to the existing default of one round and two calls.
Automatic web candidates take priority over optional local refinements for the
same eligible gap. When more needs qualify than fit, prioritize wholly missing
needs, then partial/contradicted needs, with original need order as the tie-break;
record deferred gaps. A configured zero-round recovery limit disables recovery
explicitly and must be visible in diagnostics. Do not create recursive fallbacks.

Web remains `external_web` evidence. Public sources cannot establish a private
campaign event, the character's personal history, or an unavailable selected book's
exact text. Use a public analogue only with explicit applicability limits and
edition checks. Preserve local evidence and label unresolved local gaps. Reassess
and verify web-supported claims under the existing grounding rules.

## Ten-minute calls and effective deadlines

Set the proposed default and supported ceiling to 600 seconds for each retrieval
call and each required Companion model operation, including routing, assessment,
public-query rendering, synthesis and verification. Fast calls finish immediately;
600 seconds is a maximum wait, not a delay or a correctness guarantee. Keep calls
parallel, retries bounded, cancellation effective and completed evidence intact.

Implementation must change more than the current local-tool setting:

- Update `companion_tool_timeout_s`, `companion_web_timeout_s`,
  `companion_routing_timeout_s`, and `companion_evidence_timeout_s` defaults,
  bounds, seed values and admin metadata/validation. Explicitly cap synthesis
  and verification/repair operations; a turn deadline is not a per-call timeout.
- Make `companion_retrieval_timeout_s` cover the 600-second parallel call window
  plus bounded collection/cancellation overhead. A second recovery round has its
  own window; the current twenty-second aggregate cannot remain effective.
- Derive the required turn budget from the longest permitted serial path, using
  the maximum duration of concurrent branches and the sum of sequential phases,
  plus bounded orchestration overhead. Include optional inference/replanning,
  image interpretation, both retrieval/assessment rounds, query rendering, one
  synthesis repair, verification, source validation and finalization. Validate
  `companion_turn_timeout_s` and its upper bound against that budget. A single
  600-second total turn deadline would defeat ten-minute individual calls.
- Set explicit HTTP timeouts in ShreckWorld, Chronicle and JSON-web adapters.
  Audit the ShreckLLM client, gateway `request_timeout_seconds`, provider attempt
  and logical-job watchdog limits, ShreckWorld deep-planning/review limits,
  database/graph retrieval deadlines, proxies and worker task limits. Inner limits
  must not terminate admitted work early. Do not silently raise unrelated global
  workload limits: use scoped controls where available, and document shared
  gateway operational changes where required.
- Audit the current sixty-second delegated grant validity and downstream
  authorization/revalidation points. Mint fresh grants per call/check; preserve
  originating principal expiry and revocation. Never extend user authorization
  merely to meet a timeout. Report expiry distinctly from source outage.
- Align outbox stale-worker recovery, worker leases, broker visibility, SSE
  keepalives/reconnect, and client wait limits with admitted turn duration.
  Current dispatch stale detection uses `companion_turn_timeout_s + 30`.
  Maintain fenced heartbeat writes without holding a database transaction during
  network work; worker loss must still become detectable promptly.

Record configured and effective deadlines and the layer that caused a timeout.
Snapshot settings for each admitted turn. One-time startup configuration
migrations raise shorter saved Shrecknet/gateway timeouts to the coordinated
budgets, preserving longer limits. After the migration marker is committed,
explicit administrator changes remain authoritative on future restarts.
Do not claim the new budget is active while a shorter saved setting is effective.
Optional commentary should not delay finalization. Optional rich-media work may
use a separately documented smaller budget and retain textual fallbacks.

## Delivery, verification and rollout

1. **Contracts and timeout plumbing.** Extend internal plans/prompts, preserve old
   callers, expose validated settings and diagnostics, and align worker/gateway
   limits. Test saved configuration preservation and explicit upgrade values.
2. **Plan reuse and retrieval depth.** Pass the typed call through the adapter
   boundary, reuse Elder execution, and select single/planned ShreckWorld paths.
   Strengthen memory planning/recovery relevance in the same contract change.
3. **Recovery policy.** Implement one backend scheduler in the Companion job for
   operational failures and reviewer gaps. Reuse the existing Web adapter,
   assessment, deduplication and grounding owners.
4. **Validation and documentation.** Update current-contract Companion, model
   configuration, frontend HTTP/SSE and ShreckWorld reader/agent pages when
   implemented. Update SDK models/reference/examples for any exposed additive
   diagnostics and admin config changes; coordinate ShreckOS and Foundry types
   and configuration UI. Add the release entry required by the release scope.

Focused acceptance tests must cover:

- Atlas supplied-plan execution and deterministic conversion make no second
  planner call; deep fallback is explicit; invalid/cross-ontology plans fail safely.
- Simple ShreckWorld calls perform no planning, child review or synthesis;
  complex calls retain deep coverage/provenance; selected-book and index failures
  retain their current isolation/error meaning.
- Rule explanations and casual dialogue skip memory; autobiographical and
  experience-based perspective questions retrieve it; traits still affect both.
  Include multilingual requests, follow-ups, and non-admin private-memory denial.
- Web runs on local outage/timeout/empty/non-answering evidence without a Web
  follow-up proposal. Supported needs do not trigger unnecessary recovery.
  Test disabled Web, unsafe public formulation, reviewer failure, wrong edition,
  private-history gaps, deduplication, recovery caps and mixed-source partial success.
- Simulated long calls survive past the former four/five/twelve/twenty/180-second
  limits; fake-clock tests enforce 600-second boundaries, aggregate windows,
  sequential budgets, auth expiry, cancellation, stale-worker fencing, replay and
  reconnect without waiting ten real minutes per unit test.

Run narrow tests from `shrecknet/` first, especially
`tests/test_companion_atlas_retrieval.py` and
`tests/test_companion_query_pipeline.py`, then affected Companion/config suites.
Run ShreckWorld single/planned and ontology/grant tests from `shreckworld/`, gateway
timeout tests from `shreckLLM/`, and affected SDK/client suites in their packages.
Use a deployment smoke test for real service/proxy timeout behavior and benchmark
call counts, latency, recovery success, evidence coverage and unsupported claims.

No graph rewrite, re-embedding or schema migration is required for the internal
optional fields. Configuration-data migrations upgrade stored timeouts on startup. Confirm persistence and client decoding before exposing
diagnostics. Deploy compatible adapters/contracts before activating new plans;
drain old active turns and coordinate API/worker config reloads. Roll back through
compatible code and deliberately restored timeout configuration. The inspected
repository has already retired `companion_query_pipeline_version`; there is no
runtime legacy selector to use for rollback. Longer allowed calls increase worker occupancy
and require concurrency/load validation before production activation.

Related contracts: [model configuration](./Model%20Configuration.md),
[frontend API](./Frontend%20API.md),
[ShreckWorld reader API](../../API/SHRECKWORLD_READER_API.md), and
[change/documentation policy](../../Engineering/CHANGE_AND_DOCUMENTATION_POLICY.md).
