# Embodiment model contract audit plan

**Status:** implemented; provider live-compatibility verification remains an
operational check. Initial audit findings recorded 2026-10-09. The current runtime contract remains documented in
[CharacterAgent](CharacterAgent.md); the previous reliability implementation
record describes the completed typed-consolidation and unified-validation work.

## Objective

Reduce invalid embodiment generations by making model-facing JSON contracts
represent the decisions the model must make, while keeping interpretation in the
model and derivation, references, provenance, and deterministic business rules in
the backend. Preserve public draft and persistence contracts. Ensure a failure in
one stage or scene chunk can reuse compatible validated upstream work rather than
rerunning the entire embodiment.

This is a contract and recovery audit, not a proposal to accept malformed output,
guess a value from prose, or add per-error fallback branches.

## Verified current flow

The five model-facing stages are identity description (Stage 0), scene perspective
(Stage 1), psychological enrichment (Stage 2), trait interpretation (Stage 3),
and source-level psychological consolidation (Stage 4). Stage 2 and Stage 3 run
in parallel after Stage 1 for each chunk. Stage 4 runs once per source when events
exist. Identity description is generated before scene processing when missing and
refreshed after processing.

`_call()` requests provider-native JSON Schema and validates with Pydantic. It
uses the configured shared invalid-output budget. A usage tag ending in
`.validation_retry` means a replacement generation was requested; it does not
mean the provider accepted the correction semantically. HTTP success and
`finish_reason=stop` establish transport completion only. Every accepted output
still needs backend validation.

## Contract audit findings

| Stage | Model-generated fields | Contract already visible in JSON Schema | Cross-field, contextual, or post-call rule | Simplification direction |
| --- | --- | --- | --- | --- |
| 0 Identity description | `identity_summary`, `psychological_summary`, `personality_traits[]: trait, description` | Required object keys, nonempty strings, trait enum, closed nested objects | Trait entries may repeat; no explicit registry coverage or duplicate policy. Input context has nullability and source-boundary assumptions. | Decide explicitly whether repeated trait entries are invalid, mergeable, or allowed; prefer one registry-shaped representation if exactly one entry per trait is required. Keep narrative synthesis interpretive. |
| 1 Perspective | Per position: `source_type`, `perspective` | Source-type enum and perspective length; exact outer count is injected from chunk size | Output item is associated with a scene by list position, and perspective validity depends on scene knowledge, chronology, and subjective-vs-objective distinction. These are semantic prompt rules, not machine proofs. | Keep positional binding backend-owned. Consider making only genuinely categorical decisions structured; avoid generating IDs or evidence. Retain explicit validation of count and duplicate/order invariants. |
| 2 Psychological enrichment | Per scene: emotion `arousal`, `valence`, `description`; belief `statement`, `confidence`; profile event `kind`, `description` | Numeric bounds, text bounds, event-kind enum, at most two events per scene, injected exact outer count | Whether a reaction/event is supported, lasting, identity-significant, or a real goal/aspect event depends on scene and perspective. At most one event of each kind is accepted; malformed and duplicate-kind candidates are dropped individually. | Keep emotion/belief interpretation. Retain `kind` as an explicit decision, cap each scene at one aspect and one goal event, and preserve valid candidates when another candidate is malformed. |
| 3 Trait interpretation | Per scene: `trait_candidates[]: trait, diagnostic_situation, relationship, stakes, polarity, justification` | Trait-specific diagnostic enums, context enums, polarity enum, nonempty bounded justification; max three candidates | Context must be fully specified or fully null. Malformed candidates and repeated trait keys are dropped individually; scene count/order remain strict. | Keep the typed dimensions and backend binding step. Generate provider JSON Schema from the canonical registry. Never infer a bad diagnostic from justification; select only the three strongest supported candidates. |
| 4 Consolidation | Ordered aspect/goal operation unions, local event references, existing/new indexes, focus references | Operation literals, entity-specific status enums, required/nullable operation fields, strict positive indexes, focus limited to ten, closed objects | Index availability depends on prior operations and the request snapshot. Event IDs depend on event kind. Candidate ID collisions depend on labels and current profile. Final focus eligibility depends on the reducer's final active state. These are correctly resolved through request context and shared reducers after Pydantic validation. | Retain typed operation variants, backend ID binding, strict ordered reference resolution, and shared reducers. Keep semantic reference failures within bounded correction; never make IDs or final state model-owned. Keep backend bugs outside correction. |

### Trait-context example

Today `situation_type` is a plain schema string, but runtime acceptance requires
either `unspecified` or the exact compound form
`diagnostic_situation:relationship:stakes`, where the diagnostic belongs to the
selected trait, relationship is `friend`, `enemy`, or `other`, and stakes is
`ordinary` or `high_stakes`. This explains why a successful corrective call can
still fail validation: the provider schema does not represent the conditional
contract. The correction tag identifies the retry attempt, not semantic success.

No design should treat JSON Schema as proof of scene entailment. It should encode
all structural and finite cross-field rules that are practical and provider
compatible; deterministic backend rules remain authoritative for everything
else.

## Recovery and isolation audit

The current job checkpoints merged, validated source analysis before Stage 4 and
can reuse it when the source and upstream profile fingerprints match. This is a
useful late-consolidation recovery boundary. The current design does not establish
a durable checkpoint for each successful Stage 1/2/3 chunk before all chunks for
the source finish. A failed trait interpretation chunk can therefore discard
already validated chunk results when the source attempt is retried.

Plan per-chunk checkpoints keyed by source snapshot, ordered scene IDs and input
digests, chunk boundaries, identity description, source-start profile/evidence,
prompt version, chunk size, and model target. Persist only complete validated
outputs. On retry, validate checkpoint schema and fingerprint; reuse matching
chunks and regenerate only the failed or mismatched chunk. Stage 2 and Stage 3
results for a chunk may be checkpointed independently, while Stage 1 is reusable
by both. Keep Stage 4 separately regenerable from validated source events and a
freshly loaded profile snapshot. Do not reuse a Stage 4 result against changed
profile state. No source revision or profile operation is published until all
required chunks and consolidation validate.

The checkpoint format must be versioned and invalidated when any contract,
prompt, binding rule, or model target affecting the result changes. Checkpoint
write/read failures must have an explicit policy; they must not turn incomplete
work into accepted work. Public draft and graph contracts remain unchanged.

## Proposed implementation sequence

### Phase 1 — Complete the contract inventory

- For each of the five stages, compare prompt examples, Pydantic runtime models,
  `_model_output_schema()`, deterministic binders/reducers, tests, and debug
  output. List required, nullable, bounded, unique, ordered, and cross-field
  constraints.
- Separate model decisions from backend-derived values: IDs, evidence and scene
  references, event positions, candidate identifiers, stable profile IDs, and
  final focus state remain backend-owned.
- Identify provider JSON Schema limitations using the actual supported model
  targets and fallback path; do not assume all conditional schemas are enforced.

**Exit:** a checked-in stage matrix and tests that demonstrate runtime/schema
parity for all purely structural constraints.

### Phase 2 — Redesign trait context as a typed boundary

- Add explicit model-facing diagnostic situation, relationship, and stakes fields
  with finite types. Generate trait-specific allowed diagnostic values from the
  canonical `TRAIT_BY_KEY` registry.
- Represent unspecified context with all three context fields null. Drop an
  invalid trait candidate individually, while retaining valid candidates from
  that scene. Do not infer context from justification.
- Validate trait-to-diagnostic compatibility in a single named boundary
  validator; construct the existing compact `situation_type` only after success.
- Generate provider JSON Schema from the same authoritative registry and verify
  it against the Pydantic contract and deployed provider compatibility. If a
  single conditional schema is unsupported, use a provider-compatible
  discriminated representation rather than reverting to a hidden string rule.
- Bump prompt/checkpoint contract versions together. Preserve persisted
  `TraitObservation` and `TraitEvidence` shapes.

**Exit:** every valid schema instance passes runtime structural/context
validation; invalid trait/context combinations fail explicitly; backend output
round-trips to the unchanged persistence contract.

### Phase 3 — Reconcile all five prompt and schema contracts

- Add parity tests for required keys, enums, nullability, string/list bounds,
  exact position-bound counts, uniqueness, and cross-field rules.
- Review identity trait-list duplicate policy; list uniqueness that can be
  represented structurally should be explicit and deterministic.
- Review profile event duplication and redundant enrichment outputs. Add only
  checks with clear semantics; never use automatic deduplication to hide an
  invalid model decision.
- Keep subjective scene entailment in prompts and quality evaluation rather than
  claiming it is machine-validated.
- Update every stage prompt, execution-order docstring, canonical docs, debug
  serialization, and focused tests in the same change.

**Exit:** no model-facing field has an undocumented validator contract or packs
multiple independently constrained decisions into an opaque scalar without a
documented reason.

### Phase 4 — Isolate and reuse validated chunks

- Add versioned per-chunk validated-output checkpoints under the SQL draft's
  existing checkpoint owner; avoid a parallel persistence mechanism.
- Fingerprint all canonical inputs and upstream state. Reuse only exact matches;
  invalidate on contract/prompt/model/chunk/input changes.
- Regenerate only missing or invalid chunks. Reuse validated Stage 1 output when
  retrying a dependent Stage 2 or Stage 3 call.
- Preserve the existing source-analysis checkpoint for Stage 4 retry, and bind
  every consolidation retry to the freshly verified current profile snapshot.
- Test failures at each stage, partial chunk completion, corrupt/stale
  checkpoints, model target changes, and no partial source publication.

**Exit:** a failed single chunk does not repeat successful compatible chunks;
changed inputs cannot consume stale outputs; failed Stage 4 does not rerun valid
scene chunks or publish partial profile state.

### Phase 5 — Operational verification and rollout

- Run the full CharacterAgent suite and targeted schema/provider compatibility
  checks from `shrecknet/`.
- Compare generated JSON Schema with runtime validation using valid and invalid
  fixtures for every operation variant and trait/context pairing.
- Capture live-provider quality/cost samples where available; separately report
  JSON validity, schema validity, semantic rejection rate, and retry rate.
- Drain active embodiment jobs before deploying prompt/schema/checkpoint version
  changes. Old checkpoints are invalidated, while public draft and persisted
  evidence models remain compatible. Provide rollback behavior for checkpoint
  version mismatches.

**Exit:** docs describe the active contract, operators can distinguish transport
completion from semantic acceptance, and rollback never applies checkpoints
written by an incompatible contract version.

## Test matrix

- For every stage: valid minimum/maximum outputs, missing/extra fields, enum and
  bound failures, empty optional arrays, and exact scene count.
- Trait interpretation: every trait's allowed diagnostic values; wrong-trait
  diagnostic; invalid relationship/stakes; malformed/null combinations;
  `unspecified`; duplicate trait; deterministic mapping to the legacy
  `situation_type`.
- Consolidation: missing/existing/new references, forward references,
  duplicate/colliding candidates, wrong-kind events, add-update-status sequences,
  focus against final active/inactive states, and reducer parity.
- Recovery: initial invalid output followed by invalid correction; correction
  errors preserve category; no TypeError/internal backend bug causes another LLM
  call; retry budget is exact; HTTP 200/`finish_reason=stop` does not bypass
  validation.
- Checkpointing: individually successful branches, failed sibling, failed
  consolidation, corrupted checkpoint, changed scene/profile/prompt/model, retry
  with partial compatible cache, and atomic no-publication behavior.

## Compatibility and documentation

The work should not change public HTTP payloads, SDK models, reviewed draft
shapes, graph schemas, `TraitObservation.situation_type`, or
`TraitEvidence.situation_type`. Translate the new internal trait wire shape at
the backend boundary. Document checkpoint operations and invalidation alongside
the CharacterAgent job contract. No migration is expected if the existing SQL
draft checkpoint field can carry the versioned internal structure; verify that
before implementation and document a migration only if storage needs to change.

Related pages: [CharacterAgent runtime contract](CharacterAgent.md),
[trait contract](Dispositional%20Traits.md), and
[structured output reliability implementation record](Embodiment%20Structured%20Output%20Reliability%20Plan.md).

## Execution record

The audit and first implementation pass are complete. Stage 3 now emits a
trait-discriminated candidate shape with typed diagnostic values, relationship,
stakes, and explicit all-null unspecified context. The provider schema is
generated from `TRAIT_BY_KEY`, uses provider-compatible `anyOf` branches, and
does not rely on Pydantic-only trait/diagnostic matching. The backend maps the
validated shape to the existing `TraitObservation.situation_type` contract.

Draft checkpoints now retain independently fingerprinted Stage 1 perspectives
and Stage 2/3 analyses per scene chunk, plus the merged source analysis used by
Stage 4. Successful chunks are saved before surfacing a sibling failure; a
retry validates and reuses matching chunks and reruns only missing or stale
stages. Checkpoint version 2 invalidates the previous whole-source format. The
CharacterAgent runtime and trait pages document the updated wire and recovery
contracts.

The five-stage contract matrix above records the completed code/prompt/schema
audit. Existing Stage 0, 1, 2, and 4 model representations were retained where
the audit found no clear backend derivation or a useful lower-complexity wire
shape; their semantic decisions remain explicit and validated at the current
boundaries. The plan's offline schema/runtime and retry tests pass. Live checks
against configured OpenRouter/provider model targets were not possible in this
workspace and remain part of the deployment check. The full CharacterAgent test
suite passed from `shrecknet/`.
