# Companion conversational simplification plan

Status: implemented in the repository. Live-provider quality, deployed latency and
real-table acceptance remain operational gates; see [Validation](./Validation.md).

## Goal and scope

Replace the active assessment/draft/verification loop with:

`User → Ontology resolution → LLM router → Parallel tools → Markdown synthesis → Deterministic rendering and access checks → Durable response → Asynchronous rapport`

The LLM owns semantic routing and natural language. The backend owns allowed tools,
execution bounds, evidence identities, citation resolution, source access,
presentation and durable completion. Citation validity does not establish factual
entailment; grounding is evaluated through regression fixtures and model evaluations.

Preserve character identity, psychological profile, traits, rapport, private chat
history, page/Foundry context, attachments, scope switching, cancellation, leases,
idempotency, events and asynchronous rapport ordering. Preserve Chronicle retrieval
alongside Atlas, ShreckWorld, character memory and Web. Do not introduce a second
active pipeline, a new retrieval framework or a new persistence model.

## Baseline audit and reuse

- `query_contracts.py` already defines `SimpleCompanionPlan` and `ResearchTask`.
  `planning.py` compiles them into bounded internal needs/calls. Extend this owner.
  It currently excludes Web, separately gates memory and falls back to local tools.
- `query_pipeline.py` owns scope resolution, concurrent retrieval, need-aware evidence
  aggregation, assessment/recovery, structured synthesis, verification and completion.
  Retain scope and execution machinery; remove the obsolete model stages.
- `evidence_context.py` already compacts evidence and allocates context across needs.
  Retain provenance, relevance, structured source fields and omission diagnostics.
- `contracts.py` already builds a response from prose and numeric source markers.
  Extend it rather than introducing another parser. Unknown markers currently warn;
  the new path must reject them as citations and record a distinct validation outcome.
- `rich_response.py` is deterministic but depends on `Draft`, `Segment`, `Support`
  and verifier acceptance. Replace that input dependency with resolved citations and
  source metadata. Reuse the versioned rich envelope, references and media adapters.
- `CompanionRetrieval` already provides independent tool outcomes and final source
  validation. `runner.py` and the repository own leases, terminal writes and rapport.
  Preserve these boundaries and extend typed execution errors through the runner.
- `ShreckLLMClient.chat(return_metadata=True)` already exposes `finish_reason`.
  Use this for truncation detection; verify provider-specific completion reasons.

The current `available_tools()` adds configured Web even when `tools.allowed`
excludes it. Existing tests explicitly expect this behavior. Correcting it is an
intentional permission fix, with updated API documentation and regression tests.
Several current planning/API pages also describe different pipeline generations;
reconcile them during delivery instead of treating every older plan as implemented.

## Implemented contracts and decisions

### Routing and execution

Use a minimal model-facing contract:

```json
{"mode":"search","tasks":[{"query":"Telmori character creation rules","sources":["shreckworld","web"]}],"clarification_questions":[]}
```

Modes are `search`, `chat`, `clarify`. Sources are `atlas`, `shreckworld`,
`chronicle`, `character_memory`, `web`. `search` requires tasks and no clarification
questions; `chat` requires both lists empty; `clarify` requires one to three short
questions and no tasks. All fields are required in the model contract. Queries
are nonblank and at most 500 characters. Initially retain three tasks and nine
total executable calls, with at most two Web calls per turn. Deduplicate identical
queries/source pairs and enforce limits after expansion. Reject unknown fields.

Renaming internal `question` to `query` is acceptable when all runtime producers,
tests and consumers migrate together; it does not change the public turn request.
Retain only useful internal execution fields. Remove assessment-only recovery data
after confirming saved-answer readers do not depend on it.

Intersect requested tools, configured availability, validated scope and current
authorization. `allowed:[]` executes no retrieval. Preserve acceptance of the legacy
`tools.web` field and its documented non-gating behavior; `tools.allowed` remains
the actual restriction. Never restore an excluded source through fallback.

The router decides whether personal memory is relevant; remove the separate
`companion.memory_relevance` call. Backend character binding and memory permissions
remain mandatory. Retain ontology resolution, pending clarification and explicit
scope-conflict handling. Reuse validated scope without weakening switch detection.
Do not add a model call to check the router's semantic decisions.

Run admitted calls concurrently under existing deadlines and concurrency bounds.
Keep independent adapter sessions. Preserve successful siblings when one fails.
Record `empty`, `unavailable`, `timed_out` and tool-permission rejection distinctly.
Technical router failure uses bounded permitted internal fallback; it must never
send the raw original question to Web automatically.

### Web execution policy

Web is a normal router-selected tool, executed concurrently with other selected
sources. The router may formulate any search query. No public-query catalog,
private-term filter, privacy classifier or additional query-validation model is
required. This replaces the original proposal's public-information restriction.
Configured availability, `tools.allowed`, query/call limits and safe result URL
handling still apply. Do not automatically search Web on every question.

### Evidence and synthesis

Aggregate evidence once after tool completion and proceed directly to synthesis.
Remove assessment, assessment retries, assessment-driven Web recovery, sufficiency
checks, claim extraction, structured drafts, verifier calls and repair loops.

Select bounded whole records across tasks, then assign short numeric aliases to
the exact evidence supplied to synthesis. Freeze the alias map for both generation
attempts and rendering. Never resolve an alias against a differently ordered or
larger evidence list. Preserve original stable evidence IDs in persisted manifests.

Synthesis receives the original question, resolved continuation when applicable,
bounded complete recent exchanges, identity/psychology/traits, rapport, validated
page context, evidence, provenance, epistemic categories and tool outcomes. It
returns plain Markdown with `[[source:N]]` markers. Keep existing image handling
and general-knowledge opt-in policy; neither is permission to invent mechanics.

Instructions require direct answers, distinctive character voice, meaningful factual
citations, separation of campaign canon/books/external sources/subjective memories,
and natural uncertainty for gaps or contradictions. Avoid retrieval narration and
unnecessary excerpts. Guide simple answers toward 50–150 words and explanations
toward 150–300; permit longer mechanics and exhaustive requests. A generous technical
output ceiling bounds runaway generation rather than setting normal answer length.

Empty results from one source and token-budget omissions do not automatically
invalidate other evidence or force a refusal. With no evidence, answer only what
the conversation and permitted knowledge policy support, acknowledging limitations.
Keep coverage as retrieval/context diagnostics, never a semantic correctness score.
Do not set permanent incomplete status from an earlier retrieval stage.

### Citation, generation and completion failures

Resolve only aliases in the frozen supplied map. Reject unknown/malformed identifiers
as links, remove their markers safely and surface citation diagnostics. Such an
answer must not be labeled fully cited. Missing citations and unsupported factual
claims require evaluation; identifier checks alone cannot detect them reliably.

Detect length termination from provider metadata. Retry once with the same evidence
and a shorter-answer instruction, within the remaining turn deadline. Do not
concatenate attempts or publish a truncated attempt as a completed response.
If retry also truncates or fails, return a typed generation failure with retry
information; never report that factual evidence was absent. Verify any provisional
delta behavior so retries cannot masquerade as a single completed stream.

Replace the generic evidence refusal with explicit diagnostics for generation,
citations, source authorization/revisions, context limits and optional rendering.
Tool failures remain independent outcomes. Missing evidence is a valid limited
answer condition, not a provider error. Optional media/rendering failure falls back
to cited Markdown. Required source-access/revision validation remains fail-closed.
Preserve final identity/attachment checks and leased atomic completion with the
rapport outbox. Source-derived content must not reach public events before required
authorization checks; recheck before final publication and preserve historical
redaction rules. Test revocation during generation and rendering.

### Rich rendering and public compatibility

Build a narrative block plus a small bounded set of blocks from cited sources only.
Use actual entity metadata for cards, actual structured fields for property tables,
validated book/page references for navigation and approved source media for images.
Markdown tables remain in the narrative. Avoid automatic long excerpts and copying
the narrative into cards. Galleries require multiple relevant authorized images.
No rendering model call, invented references or invented URLs are permitted.

Keep `result.text` readable without rich rendering, version-1 blocks/targets/assets,
citations, evidence, warnings and tool outcomes. Emit one `conversation` segment
for new Markdown answers; retain readers for historic factual/reaction segments.
Inventory actual response schemas, stored JSON, answer-details projections, SDK
models and Foundry renderers before retiring fields. Keep compatibility fields
nullable/empty where consumers require them, without retaining runtime assessment
or verifier contracts. ShreckOS changes belong in its owning repository.

Use pipeline status events for progress. Retain at most the existing brief static
acknowledgement; remove repeated generated personality commentary. Add stage/tool
durations, selected/admitted tools, statuses, LLM usage, response length, citation
validation, retries and generation error metrics. Do not log private queries or
source text through diagnostics.

## Delivery sequence (completed)

| Milestone | Deliverable | Acceptance |
| --- | --- | --- |
| 1. Contract and consumer inventory | Pin preserved request/result/event contracts, historic fixtures, Web permissions and adapter error mapping. | SDK/Foundry consumers identified; Web admission fixtures and explicit compatibility expectations ready. |
| 2. Router and normal Web execution | Extend simple schema/compiler, enforce allowlists/call limits, remove memory vote, retain scope behavior. | Search/chat/clarify, internal-only rules, Chronicle, mixed sources, forbidden Web and character-memory isolation tests pass. |
| 3. Markdown pipeline | Remove assessment and verifier from execution; freeze citation aliases; add metadata-aware synthesis and bounded truncation recovery. | No obsolete stage calls; partial tools still answer; invalid citations and generation failures are distinguishable; access checks remain mandatory. |
| 4. Deterministic rich rendering | Replace draft/support inputs with resolved citations; preserve navigation and envelope. | Cited text survives missing media/render failures; no fabricated fields or repeated long excerpts; rendering uses no model calls. |
| 5. Cleanup, evaluation and rollout | Delete obsolete runtime branches/prompts/contracts after migrating tests, reconcile docs/configuration and benchmark. | Required regressions, affected suites and consumer fixtures pass; real-provider acceptance and rollback steps recorded. |

Milestones are reviewable increments of one implementation. Update schemas, prompt
definitions, maintainer headers, validators, tests and ordered prompt documentation
together. Do not deploy schema/prompt/worker versions independently.

## Verification and acceptance

Migrate focused suites including `test_companion_planning.py`,
`test_companion_query_pipeline.py`, `test_companion_evidence_selection.py`,
`test_companion_generation.py`, `test_companion_rich_response.py` and
`test_companion_answer_details.py`. Replace obsolete assessment tests with relevant
aggregation/generation regressions; do not just delete their failure coverage.

Required scenarios:

1. Telmori creation uses internal plus router-selected Web evidence and produces a
   useful cited answer without generic refusal. Use a controlled fixture reproducing the structured-generation failure condition;
   evaluate real rules/edition grounding separately with the deployed model.
2. Internal rules choose ShreckWorld without unnecessary Web.
3. Campaign questions can select Atlas/Chronicle without unnecessary Web; any
   router-generated Web query may execute when the tool is allowed.
4. Character memories remain character-bound and explicitly subjective.
5. Casual chat performs no retrieval and preserves personality/rapport inputs.
6. Failed/empty/unavailable siblings do not erase useful evidence.
7. Length termination retries once with identical evidence; second failure stays
   a generation failure. Validate aliases after context selection and deduplication.
8. Optional media or rich composition failure preserves correctly cited Markdown.
9. Missing/contradictory evidence yields honest limits without invented rules.
10. Revoked/revised sources cannot be published or reused, including during retries.

Also cover empty allowlists, forged tool names, call caps, pending scope continuation,
prompt injection, malformed markers, general-knowledge opt-in, context overflow,
attachments, cancellation/commit races, historical answer reads and exactly-once
rapport enqueue. Assert removed model usage tags never execute.

Run narrow tests from `shrecknet/`, then the affected package suite when practical;
run SDK tests from `python_sdk/` and Foundry tests from its module directory. Run
`shreckLLM/` tests if gateway/provider behavior changes. Distinguish deterministic
mock tests from real-model grounding/personality evaluations. Benchmark model-call
count, tool latency, completion latency, usage, citation validity and grounding on
fixed sanitized scenarios; avoid unsupported latency or semantic-quality claims.

## Documentation, operations and rollback

Update `Companion.md`, `Frontend API.md`, `Model Configuration.md`, `Validation.md`
and relevant pipeline/planner/recovery/rich-response design pages. Mark replaced
plans historical and link this design; keep current behavior pages accurate until
implementation lands. Update the documentation index as page purposes change.
Update root/package README descriptions, SDK references/examples and release notes
where their current assessment/verification/configuration claims change.

Retain existing saved model/config keys for compatibility; mark assessment-only
roles/settings inactive where applicable rather than silently repurposing them.
Audit timeout calculations that reserve removed calls. Keep asynchronous rapport
and provider-backed Web roles active. No database migration or re-embedding is
expected, subject to the consumer inventory; persisted historic results stay readable.

Drain active turns and deploy matching API/workers, prompt/schema code and required
consumers together. Test canary scenarios before broad enablement. Rollback disables
new admission, drains workers and restores the previous compatible application;
it must preserve new conversation/rapport writes and historical result readability.
Do not introduce a runtime switch keeping both pipeline implementations active.

Local verification and remaining deployment gates are recorded in
[Validation](./Validation.md). Provider-double regressions do not certify semantic
truth, live model quality or deployed performance.

## Related documentation

- [Current Companion behavior](./Companion.md)
- [HTTP and event contract](./Frontend%20API.md)
- [Model configuration](./Model%20Configuration.md)
- [Validation](./Validation.md)
- [Simple planner](./Simple%20Research%20Planner%20Plan.md)
- [Rich response design](./Rich%20Response%20Plan.md)
- [Change and documentation policy](../../Engineering/CHANGE_AND_DOCUMENTATION_POLICY.md)
