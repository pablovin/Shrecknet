# Companion query pipeline redesign plan

Status: implemented behind `companion_query_pipeline_version="evidence_v2"`;
live provider, production latency and human grounding acceptance remain deployment gates.
This extends the [Companion backend plan](Companion%20Backend%20Plan.md).
The [Companion guide](Companion.md), [frontend API](Frontend%20API.md), and
[model configuration](Model%20Configuration.md) describe the implemented contract.

The optional [rich response extension](Rich%20Response%20Plan.md) adds a shared
ShreckOS/Foundry display contract and media resolution inside this pipeline's
verification gate. It preserves buffered factual publication, durable commentary,
scope/permission rules and text fallback. Rich composition is implemented behind
`companion_rich_response_enabled=false` by default and shares factual verification.

## Goal and scope

Follow the requested flow: user question, history and Foundry/page context →
explicit/saved ontology fast path (inference only if missing) → LLM routing →
optional Ontology update and replanning → independent parallel retrieval tools →
evidence assessment and bounded recovery → grounded character synthesis →
claim verification → answer, citations and diagnostics.

The owner is Character Companion (`/users/me/character-companion`), including
ShreckOS and Foundry clients. The legacy personal companion orchestrator and
ShreckCompanion notification settings are separate features. Preserve Chronicle
retrieval and subjective character memory alongside the three primary sources:
Atlas, ShreckWorld, and Web. They remain subject to existing tool permissions.

Use deterministic code for authorization, scope validation, timeouts, output
validation and persistence. Use a small LLM for ambiguous ontology interpretation
and for tool/query planning; model confidence never grants source access.

## Existing implementation and changes needed

| Owner | Existing behavior | Planned change |
| --- | --- | --- |
| `app/services/character_companion_service.py` | Admission resolves context and can substitute the character ontology | Preserve explicit context provenance; resolve conversational ontology separately |
| `app/jobs/companion/runner.py` | Keyword routing; optional model only when no keyword route matched | Mandatory structured planner in the new pipeline; no keyword fallback |
| `app/integrations/companion/retrieval.py` | Concurrent bounded adapters; one shared query for most sources | Independent planned queries and per-call outcomes, preserving authorization |
| Atlas adapter | Shared Elder retrieval executor; fixed hybrid-search plan | Reuse Elder planning/retrieval in evidence-only mode; no second public answer |
| ShreckWorld adapter | Evidence endpoint with `retrieval_strategy="single"` | Use existing `planned` evidence pipeline and preserve coverage/need provenance |
| Web adapter | Provider search uses `model_companion_synthesis`; optional JSON provider | Dedicated configurable web model, retaining legacy adapter compatibility |
| `app/jobs/companion/contracts.py` | Citation marker integrity; one conversation segment | Typed factual claims, actual support checks, honest coverage and fallback status |
| `app/repositories/character_companion_repository.py` | One user and one assistant message per turn | Durable commentary messages plus one final reply; explicit message kinds |
| ShreckOS `shreckos/src/features/companion/` | Status SSE; interim cleared on completion | Persistent commentary and a compact status beneath the current response |

The frontend repository inspected is `/home/pablovin/workplace/ShrecknetFrontend`.
Its active ShreckOS Companion implementation is under `shreckos/src/`, rather
than the older top-level frontend. Use the active Administration Shrecknet /
Companion group and its model selectors. Do not put these settings in the legacy
`ShreckCompanionConfig.tsx` notification page.

## 1. Normalize the question and resolve ontology

Inputs: original question, recent completed exchanges, separately typed durable
commentary, explicit page context, Foundry snapshot, pending clarification, and
the chat's last validated ontology selection. Retain the original spelling and
case. Bound all context; inspect attached images through the existing private
image path when needed. Foundry documents remain user-provided observations.

Store `resolved_ontology` separately from `default_scope`, with ontology ID, name,
description, RPG system/edition when available, resolution source and revision.
Also store bounded `pending_clarification` state on the chat. Character identity
and character-memory scope remain bound to the selected character.

Resolution order and rules:

1. Validate explicit per-turn/page or Foundry ontology IDs using authoritative
   ontology records, membership and existing access rules, save the selection,
   and go directly to the router. Skip ontology inference entirely: no ontology
   LLM call or selection question is needed for a valid explicit ID. A Foundry
   World with multiple member ontologies is not itself an ontology selection.
   Invalid/inaccessible explicit IDs produce a scope error rather than inference.
2. With no explicit ID, reuse the saved chat selection after checking its current
   existence/access, and go directly to the router. Do not infer the ontology
   again on every turn. The router can request an Ontology update below.
3. Only when neither supplies a usable selection, call
   `model_companion_routing` with the question/history, bounded
   context and authorized ontology candidates. Return `resolved`, `ambiguous`,
   `unresolved`, or `not_needed`, a candidate ID or null, and a short reason.
   Backend validation must confirm every proposed ID. Do not select the
   character's ontology merely because it exists.
4. If ambiguous/unresolved, ask by ontology name: “Which world do you mean:
   A or B?” Names need not be unique; add safe distinguishing descriptions.
   Never expose unauthorized candidates. If many exist, use bounded candidate
   lookup and ask for a name rather than sending an unbounded catalog to a model.

Greetings, personal reactions and clearly world-independent public questions
can use `not_needed`. World-dependent queries cannot proceed without resolution.
After an ontology is set, switch detection belongs to the normal router, not a
repeated ontology-resolution stage. Uncertainty about a requested switch returns
to clarification rather than searching the old world.

Clarification completes the current turn with `outcome="needs_clarification"`,
commits its question and releases the chat lock. No worker waits for user input.
The next ordinary turn resumes the original pending question with the answer,
unless the user cancels or changes topic. Revalidate access and take current
settings/source snapshots when resuming. Clarification uses the synthesis model
for character voice with search disabled; use a direct template if it fails.

## 2. Replace keyword routing with an LLM retrieval planner

Use the existing `model_companion_routing` role for a separate, small structured
call after resolution. Explicit resolution and reuse do not need another ontology
LLM call. Inputs include the question/history, validated ontology metadata,
available sources and their scopes, Foundry context, and tool/web permissions.

Proposed validated plan shape:

```json
{
  "action": "retrieve",
  "ontology_update": null,
  "needs": [{"id": "n1", "question": "What must be established?"}],
  "calls": [{"id": "c1", "tool": "atlas", "query": "Targeted question", "need_ids": ["n1"]}],
  "clarification_questions": []
}
```

`action` is `retrieve`, `ontology_update`, `clarify`, or `conversation`. Retrieval tool values are `atlas`,
`shreckworld`, `web`, `chronicle`, `character_memory`; only backend-approved
available tools may be selected. Require unique bounded IDs, nonblank bounded
queries, valid need references, at most six needs and four calls by default.
`retrieve` requires calls; `clarify` requires concise questions and no calls;
`conversation` requires neither calls nor research needs. These bounds must be
expressed in prompts and schemas, not just Python checks.

### Ontology update: a fourth primary router tool, executed first

Expose **Ontology update** (`ontology_update`) alongside Atlas, ShreckWorld and
Web in the router's tool descriptions. It is a scope-changing control action,
executed before any retrieval rather than in the parallel retrieval task list.
It is available when the chat already has an ontology. The router selects it
when the user requests a different world, for example “Switch to Ravenloft;
who rules this city?” Do not switch merely because another world is mentioned
as part of a comparison or quoted source.

For `action="ontology_update"`, `ontology_update` must contain
`{"ontology_name":"Ravenloft","reason":"User requested a world switch"}`;
both strings are nonblank and bounded (255 and 500 characters respectively).
For every other action it must be null. Update actions require empty `calls`,
`needs`, and `clarification_questions`; the original question is retained by the
backend, not reconstructed from a model-generated replacement. The model proposes
a name, never a trusted ontology ID. Execute this sequence:

1. Resolve the name against authorized ontology records. Ask for clarification
   if there are multiple matches or no identifiable accessible target. Do not
   update the chat or retrieve from its old ontology while clarification is pending.
2. If the requested target conflicts with an explicit Foundry/page ontology on
   this turn, ask which scope the user intends before updating. An explicit user
   choice can override page context through a stored scope override; show the
   active scope in the client and keep that override effective until the user
   selects another scope or chooses “Use page/Foundry context”. Do not silently
   change the bound Foundry table or bounce back on its next snapshot.
3. Validate and atomically save the new chat ontology with a revision check and
   active-turn lease. Clear incompatible library/source IDs, page selections,
   Chronicle bindings and pending retrieval state; invalidate old-scope caches.
   Refresh system/edition metadata and available tools. Character identity,
   rapport and subjective character-memory scope do not change.
4. Persist the new validated execution context before issuing grants. Emit a
   durable `ontology.updated` event containing safe old/new scope metadata and
   show “Switched to Ravenloft” in status. Replaying the event or retrying the
   turn must not repeat the update or increment revisions twice.
5. Run the router again with the new scope and the original question/history,
   excluding `ontology_update` for this second pass. Only then dispatch planned
   retrieval in parallel. Permit at most one ontology update per turn; a further
   switch request becomes clarification, preventing routing loops. A switch-only
   request ends with a short confirmation and no retrieval.

If an update fails, stop with clarification or a safe error; never answer from
the former world as though the switch succeeded. Once committed, the selection
persists even if subsequent retrieval fails or the turn is cancelled; report
the actual saved scope. Cancellation before commit prevents the update. History
from previous ontologies remains history, with its scope recorded; its citations
cannot be reused as evidence for the newly selected world.

The model chooses tools and queries, never credentials, endpoints, grants or
ontology/source IDs. The backend pins all calls to the resolved authorized scope.
“Ask the user” is a planner action, not a fake retrieval source. Ask about the
missing information (for example rule edition or character identity), rather than
requiring the user to understand internal tool names. Recover contextual
follow-up intent through this planner, replacing the current English-prefix
heuristics. Planner timeout/invalid output produces a safe clarification or
explicit inability to plan; it must not silently become a factual conversation.

## 3. Execute source pipelines independently in parallel

| Tool | Pipeline and result |
| --- | --- |
| Atlas | Shared Elder planner/executor, restricted to resolved ontology; graph/canon evidence and provenance, no Elder public synthesis |
| ShreckWorld | Existing planned evidence retrieval, requested books within resolved ontology, system/edition constraints, index readiness and per-need coverage |
| Web | Dedicated model through ShreckLLM provider search; public queries, source URLs and actual provider excerpts; generated summaries are not evidence |
| Chronicle | Existing authorized Chronicle retrieval, preserving explicit Chronicle scope |
| Character memory | Existing selected-character ranking; subjective perspectives explicitly labeled |

Use one task per planned call, with its own ID, query, needs, timing and outcome.
Normalize `ok`, `empty`, `timed_out`, `unavailable`, `failed`, `cancelled` and
`skipped` internally; map additive public details without breaking old status
enums. An unexpected adapter error must not cancel successful siblings. Do not
share database sessions between concurrent tasks. Reuse existing grants and
revalidate admitted scope; resolving an ontology must be persisted in the
server-owned execution context before granting downstream access.

An overall deadline cancels only unfinished work and retains completed evidence.
Do not turn unready indexes, access failures or timeouts into empty results.
Preserve complete evidence units, stable evidence IDs, source scope, revisions,
system/edition, source counts and omitted-budget diagnostics. Cache keys include
resolved scope, query, source subset and revision; retain current ShreckWorld and
Foundry cache restrictions.

Provider web search defaults to enabled on its dedicated model target. Existing
turn-level `tools.web` (`disabled`, `allow`, `require`) still gates execution;
default model capability does not override user permission. If required web is
unavailable, report that explicitly. Public query planning must not append private
Foundry names, chat history or campaign facts to web queries automatically.
If private details are necessary, ask before submitting them externally.

## 4. Assess evidence and recover missing information

Use `model_companion_evidence` for a single batched review of all requested needs.
Return per-need `supported`, `partial`, `missing`, `contradicted` or `unverified`,
supporting evidence IDs and exact excerpts, conflicting evidence, and optional
targeted follow-up queries. Validate IDs and excerpt membership deterministically.
Check relevance and entailment through the reviewer; matching a quote alone
does not establish that it supports the claim.

Distinguish “searched available sources” from “proved exhaustive coverage”.
Subjective memory, Foundry snapshots, canon, rulebooks and external web retain
their epistemic categories. A generic web rule does not establish campaign canon
or silently override a selected book/edition. Surface unresolved contradictions.

Allow at most one Companion recovery round by default, only for specific missing
or contradictory needs, subject to remaining time, tokens and permissions.
Run independent recovery calls concurrently; preserve initial evidence. Reassess
once after recovery. Never expand scope automatically. ShreckWorld already has
its own bounded recovery: carry its trace into Companion review, do not repeat
the same search just because it returned an incomplete result. Exhausted budgets
remain explicit gaps. Reviewer failure means `unverified`, never complete.

## 5. Synthesize and verify the response

Synthesis receives validated evidence, need coverage, contradictions, source
outcomes, identity summaries, effective traits, subjective memories, rapport and
bounded completed conversation. Use `model_companion_synthesis`, search disabled.
Do not let a prompt invent character facts or promote memories to canon.

Generate structured answer segments with IDs and a kind: `factual`, `reaction`,
`uncertainty`, `clarification`, or `general_knowledge`. Each factual segment
declares atomic claims and evidence IDs. A batched verification call using
`model_companion_evidence` checks each claim against its cited passages for
support, relevance, scope and contradictions. Deterministic validation checks
known IDs, quotations and source revision/access. Verification is a model-assisted
check, not a mathematical guarantee; retain human grounding acceptance tests.

Uncited personal reactions (“That sounds dangerous”) are allowed. Embedded factual
claims still require support regardless of their segment label. Verify this
classification too. Reject unsupported claims, attempt at most one bounded repair,
then omit them or render a clear uncertainty statement. On verifier failure,
return a safe limitations response with available evidence, not an unverified
factual draft. Coverage cannot be complete after omitted claims or failed tools.

Default `companion_general_knowledge_policy="disabled"`. Optional
`"labeled_opt_in"` requires both administrator enablement and an explicit per-turn
request or user acceptance of a fallback offer. Render “General knowledge — not
verified against your world or books”, with no fabricated source links. It must
not claim canon, exact rule text, an exhaustive search or absence of a rule.
Conversation and personal reactions do not need this factual fallback policy.

Buffer factual synthesis until verification succeeds, then publish/stream the
accepted response. Do not display unsupported factual tokens and try to erase
them later. Immediate status and durable commentary provide responsiveness while
the draft is private. Keep final source/identity/session revalidation and atomic
final reply/rapport-outbox commit.

## 6. Keep the user informed with status and permanent commentary

Show a compact status below the active Companion response, updated from durable
stage/tool events: “Choosing the world”, “Planning the search”, “Checking Atlas
and rulebooks”, “Comparing sources”, “Looking up a missing detail”, “Preparing the
reply”, “Checking citations”. Tool completion/failure is visible without making
users open diagnostics. Do not expose prompts, reasoning or raw provider errors.
Completed turns retain a short outcome and expandable timing/source diagnostics.

Commit an immediate character-appropriate acknowledgement without waiting for
an LLM, then optionally generate short contextual skits using
`model_companion_interim` concurrently. Give the interim prompt identity, current
stage and safe known context; it cannot announce findings it has not received.
Default to no more than two commentary messages per turn, at least eight seconds
apart. Skip model skits if the turn finishes quickly. Skit failure never blocks
retrieval. Timing/budgets are configurable. Cancel unfinished skit generation when
the turn ends, but preserve every commentary message already delivered.

Durability is a backend contract: add `kind` and `sequence` to
`CompanionMessage`; existing messages become `user`/`final` with sequence zero.
Replace uniqueness on `(turn_id, role)` with `(turn_id, role, sequence)` so
multiple assistant commentary messages and one final can coexist. Reserve
assistant sequence zero for the final; positive sequences identify commentary,
ordered for display by creation time and ID. Persist each commentary and its
event atomically before emitting it, fenced by the active lease. Reconnects
deduplicate by message ID. Message pagination and turn snapshots expose delivered
commentary even after SSE retention expires and after failure/cancellation.

Final-only context and rapport queries must explicitly filter message kinds;
commentary is display history, not evidence or an additional completed exchange.
Keep pending user context visible alongside commentary on failed turns without
treating it as a completed exchange. Existing deletion/privacy cleanup applies.
Clarification questions are final replies, not skits. Rapport should treat purely
operational clarification turns as audited no-ops, preserving completion ordering.

## Model roles and frontend settings

Keep the repository's `model_companion_*` naming and `LLMModelTarget` shape.
Reuse routing for ontology inference and planning, synthesis for clarification,
and interim for skits; add only web and evidence model roles.

| Setting | Role | New-install default |
| --- | --- | --- |
| `model_companion_routing` | Fast ontology inference and retrieval planner | OpenRouter `deepseek/deepseek-v4.1-flash` |
| `model_companion_web_search` | Dedicated web evidence tool | Same model; `web_search_enabled=true` |
| `model_companion_evidence` | Coverage assessment and final factual verification | Same model |
| `model_companion_synthesis` | Character reply, clarification and bounded repair | Existing default, same model |
| `model_companion_interim` | Brief permanent character commentary | Existing default, same model |
| `model_companion_rapport_update` | Async completed-exchange relational evaluation | Existing default, same model |

All default to reasoning disabled and null effort. Non-web calls forcibly disable
search even if saved target flags differ. Reuse current gateway capability checks;
an unsupported search target is a configuration error, not an implicit searchless
web answer. These are existing-repository model defaults, not a new assertion
about current external model availability; verify provider availability at rollout.

Expose the six role selectors in **Administration → Shrecknet → Companion**
using the existing backend schema group `companion_agent`, provider catalogs,
model selectors, reasoning controls and capability/status badges. Group controls
as Models, Retrieval and verification, and Progress and commentary. Include:

- `companion_query_pipeline_version`: `legacy` / `evidence_v2`; default
  `evidence_v2` for new/missing settings, preserving explicit saved selections. In v2 routing is required; the old optional-routing switch applies
  only to legacy operation and must not silently disable the v2 planner.
- `companion_general_knowledge_policy`: `disabled` / `labeled_opt_in`.
- `companion_recovery_max_rounds`: default 1, allowed 0..1;
  `companion_recovery_max_calls`: default 2, allowed 1..4.
- `companion_routing_timeout_s`: default 3;
  `companion_evidence_timeout_s`: default 8;
  `companion_web_timeout_s`: default 12. Validate positive bounded values against
  the turn deadline; use existing timeouts for other sources and total execution.
- Existing `companion_tool_timeout_s`, `companion_retrieval_timeout_s`,
  `companion_turn_timeout_s`, evidence/output budgets and trusted service URLs.
  V2 proposed initial overall retrieval budget: 20 seconds, measured before
  rollout; the current 8-second aggregate would undercut a 12-second web call.
- Existing `companion_interim_enabled` (v2 new installs: true),
  `companion_interim_max_messages` (2, range 0..3),
  `companion_interim_min_interval_s` (8), and skit output budget. Immediate
  acknowledgement counts against this maximum; status remains enabled at zero.

Show that model-level web search defaults on, while actual execution requires
turn permission. Preserve all explicitly saved targets and flags. Seed new roles
only when absent; never reset an installed configuration. On upgrade, explain
that the old synthesis search flag no longer controls v2 web retrieval; select
the dedicated web role explicitly before switching pipelines. Legacy JSON search
settings remain available for compatibility; the selected backend and its
capability must be explicit, with no silent fallback to another provider.

Update config target allowlists, seed JSON, schema metadata, PUT validation,
`/llm_status/`, user-safe `/capabilities`, SDK model configuration helpers and
frontend types together. Record effective targets and configuration version in
turn diagnostics. Snapshot settings at worker entry; coordinate API/worker
reloads, since current settings can be process-cached.

## Ownership, public contracts and prompt organization

Keep orchestration in `app/jobs/companion/`; split focused resolution, planning,
assessment and synthesis modules only as needed. Keep authoritative ontology
lookup/selection rules in services and persistence operations in the existing
repository. Adapters own external calls; HTTP routers map contracts only.
Extend shared Elder/ShreckWorld owners rather than cloning their retrieval logic.

Add typed chat resolution/pending state, per-turn resolution/plan/assessment,
per-call diagnostics, claim support, response outcome and commentary metadata.
Persist the original submitted request unchanged for idempotency and store the
resolved execution context separately; update retrieval-grant authorization to
read that validated context in v2. Switching scope must never alter the
idempotency hash of the original request. Clarification continuation carries a
pending-question ID and is version checked to reject stale answers.

Keep existing coarse `TurnRead.status` values for compatibility. Add a stage
field/event for ontology resolution, planning, assessment, recovery and claim
verification instead of extending the existing status enum unnecessarily.
Include ontology update/replanning stages, the durable `ontology.updated` event,
and the effective scope override in chat/turn snapshots and client types.
Snapshots contain the current stage and delivered commentary. Extend existing
SSE transport with versioned progress and durable-message events; preserve
legacy routing/retrieval events until clients have migrated. New message kinds,
claim fields and tool outcomes require coordinated API/SDK/frontend decoding.

Prompts live beside their jobs and appear in execution order: ontology inference,
retrieval planning, optional ontology update and replanning, optional image
interpretation, parallel web and commentary,
assessment/recovery, clarification or synthesis, claim verification/repair;
asynchronous rapport remains last. For each prompt, document call site, full
bounded input/output JSON, enums, nullability, identifiers and stage/public-output
rules. Keep recovery variants beside normal prompts. Update canonical flow docs,
Pydantic schemas and deterministic validators in the same implementation change.

## Delivery order and acceptance

1. **Contracts/configuration/migration.** Add versioned pipeline, model roles,
   typed execution context, pending clarification and durable-message migration.
   Add admin discovery/validation and SDK support. Test preservation of saved
   config, schema metadata, search capability and idempotency replay.
2. **Resolution and planning.** Implement explicit scope, authorized candidate
   inference, saved chat reuse, switches and clarification continuation. Replace
   keyword routing in v2. Test ambiguous/duplicate names, no candidates, revoked
   selection, Foundry multi-ontology worlds, conflicting page context, unrelated
   character ontology, multilingual follow-ups, malformed model output and timeout.
   Assert zero ontology-inference calls for valid explicit and saved scopes.
   Test router-requested updates run before every retrieval, queries/grants use
   only the new scope, ambiguity prevents old-scope retrieval, explicit-context
   conflicts and persistent overrides, one-update limits, switch-only turns,
   idempotent replay, revision races and cancellation before/after update commit.
3. **Parallel tools.** Wire source-specific queries to Elder, planned ShreckWorld
   and the dedicated web role. Test independent exceptions/timeouts, partial
   success, grants, index readiness, query privacy, web permission and cache scope.
4. **Assessment/grounding.** Add coverage, contradictions, one recovery round,
   factual verification and explicit fallback policy. Test invented citations,
   real citation supporting the wrong claim, irrelevant exact quotes, wrong
   world/edition, unverified review, missing coverage, subjective-memory conflicts,
   uncited reactions containing facts and repair exhaustion.
5. **Visible progress and persistent skits.** Update ShreckOS provider/panel/API
   types and Administration configuration; update Foundry rendering. Test status
   transitions, message ordering, reload/replay after event expiry, duplicate
   events, failure/cancellation, deletion, stale worker fencing, and exclusion of
   skits from rapport/context. Never erase delivered commentary at finalization.
6. **Documentation and rollout.** Update canonical Companion flow, model settings,
   frontend HTTP/SSE and Foundry contracts, SDK docs/reference/examples, validation
   guide and this documentation index. Add a release entry per release process.

Run focused tests from each package directory, then affected suites when practical:
Shrecknet Companion, Foundry context, answer details, chat sessions, config and
gateway-search tests; ShreckWorld planned query/ontology/grounded-answer tests;
shreckLLM search capability tests; Python SDK Companion/config tests; ShreckOS
Companion/admin tests; Foundry Companion tests. Add targeted new tests rather
than relying only on the existing suites. Typecheck/build affected clients.

Benchmark a fixed corpus of canon, rules, mixed-source, ambiguous-world,
contradiction, no-evidence and casual-chat turns. Measure queue time, first visible
status, first commentary, final verified reply, per-stage p50/p95, tokens, cost,
coverage and unsupported-claim rate. Proposed UX targets: acknowledgement/status
within one second of admission under normal load; ontology/planner calls each
within three seconds; ordinary verified turns p50 under 12 seconds and p95 under
30 seconds. These are acceptance targets, not measured guarantees. Slow source
budgets and queue saturation require separate load acceptance.

Back up SQL and runtime config; apply an explicit versioned migration before new
workers (startup `create_all` cannot alter the deployed message constraint).
Backfill old message kinds/sequence without changing text or ordering. Deploy
compatible gateway, services, API/workers, SDK and clients together. V2 defaults
on for new/missing configuration; explicitly select legacy beforehand if a canary
rollout requires holding activation. Existing chats resolve on their next relevant turn; do not
mass-select their character ontology. No graph rewrite or re-embedding is needed.

Drain active turns before switching pipeline versions. Roll back through
compatible images and the legacy pipeline switch; retain additive data. Because
multiple assistant messages invalidate old repository assumptions, rollback to
older binaries requires a compatibility release that filters final messages;
do not recreate the old unique constraint over durable commentary or delete
messages to force rollback. Gate production activation on migration, replay,
provider availability, grounding evaluation and latency acceptance.
