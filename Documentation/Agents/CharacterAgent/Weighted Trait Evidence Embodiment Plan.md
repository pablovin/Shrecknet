# Weighted Trait Evidence Embodiment Plan

**Status:** implementation record; compact extraction, weighted scoring, event
grouping, baseline/evolving Steadiness, dual-version history, SDK models, and
focused regression checks are implemented. Live model comparison and pilot
acceptance remain operational checks. See [Dispositional Traits](Dispositional%20Traits.md)
for the current runtime contract.

## Purpose and scope

Simplify CharacterAgent embodiment Stage 3 and admit scene-anchored subjective
preferences as lower-weight evidence. Keep one observation per trait per canonical
scene, one normal Stage 3 call per chunk, deterministic scoring, and the existing
eight dimensions. Redefine Steadiness as observed cross-scene dispositional
consistency, initialized at 5 with an explicit `baseline` status.

This proposal replaces the separate experiential-channel approach in
[Experiential Trait Evidence Plan](Experiential%20Trait%20Evidence%20Plan.md) for
this implementation. That earlier document is retained as a design alternative;
its additional candidate channel and unscored hypothesis reducer are out of scope.
The earlier separate-channel proposal is not implemented. The current contract is
[Dispositional Traits](Dispositional%20Traits.md).

## Current implementation and owners

- `jobs/character_agent/embody_agent.py::_interpret_traits_batch` already receives
  ordered canonical scenes and validated Stage 1 perspectives. It runs alongside
  Stage 2 psychology and binds scene/perspective references in the backend.
- `embody_agent_prompts.py::TRAIT_INTERPRETATION_PROMPT` and
  `trait_evidence_contract.py` currently require canonical diagnostic evidence and
  expose per-trait situation-context enums.
- The private `_TraitCandidateLLMOutput`, `_TRAIT_SLOT_MODELS`, provider-schema
  shaping, and candidate sanitizer in `embody_agent.py` enforce that contract.
- `schemas/character_traits.py` owns public/profile/evidence contracts and versions.
  `services/character_trait_service.py` owns grounding, replay, scoring, manual
  overrides, and history calculations.
- `services/character_trait_analysis.py` supplies evidence to final identity
  synthesis and verifies Steadiness for synthesis and decisions.
- `services/character_embodiment_service.py` loads prior evidence, skips processed
  scenes, and manages drafts/checkpoints. `character_agent_service.py` persists
  revision-owned JSON ledgers and exposes history and administrator-only evidence.
  `tasks/character_embodiment.py` remains orchestration only.
- `python_sdk/shrecknet_client/character_traits.py` mirrors strict public models;
  evidence changes must reach it in the same release.

## Proposed Stage 3 contract

Each input scene returns all eight required nullable slots in the same order.
Every non-null slot has exactly `polarity`, `source`, and `reason`:

```json
{
  "scene_trait_interpretations": [{
    "integrity": null,
    "caution": {
      "polarity": "high",
      "source": "perspective",
      "reason": "Johnny favors the diluted rite to avoid being claimed by Ishtar."
    },
    "presence": null,
    "forbearance": null,
    "diligence": null,
    "curiosity": null,
    "sharing": null,
    "restlessness": null
  }]
}
```

`polarity` is `low` or `high`; `source` is `canonical` or `perspective`.
`reason` is nonblank and at most 360 characters. Additional keys are forbidden.
The example is admissible only if the actual supplied scene and Johnny's
perspective support that preference; it is not a fixture expectation by itself.
The model supplies no IDs, contexts, strengths, confidence, scores, or Steadiness.

| Source | Admission | Backend weight |
| --- | --- | --- |
| `canonical` | Directly documented behavior or explicit statement by this character that discriminates a pole | 2 |
| `perspective` | A character-specific preference, motivation, or behavioral tendency expressed in the supplied perspective and anchored to the actual scene | 1 |

Evaluate all eight traits independently. Select canonical evidence when available;
agreement with a perspective adds no second observation. If the channels disagree,
canonical evidence takes precedence. If the strongest channel itself supports
opposing poles with no defensible dominant direction, return null for that trait.
Use null when neither channel establishes a meaningful directional signal.

Perspective evidence may interpret the scene but cannot invent an action,
participant, alternative, or event, contradict canonical facts, transfer another
character's behavior, or merely repeat a prior personality description. A specific
preference can qualify without a canonical outward action; generic fear or task
participation alone does not establish a tendency. Provenance validation checks
binding and shape, not semantic truth; frozen-case review must assess grounding.

### Compact definitions

| Trait | Low | High |
| --- | --- | --- |
| Integrity | Exploits others for personal gain | Rejects unfair advantage, even at a cost |
| Caution | Fearless, little need for reassurance | Sensitive to threats, seeks security and reassurance |
| Presence | Avoids social attention and company | Socially bold, seeks interaction and visibility |
| Forbearance | Retaliates, holds grudges | Forgives, compromises, tolerates provocation |
| Diligence | Cuts corners, impulsive, disorganized | Plans, persists, fulfills obligations thoroughly |
| Curiosity | Prefers familiar ideas and experiences | Explores unfamiliar ideas and information |
| Sharing | Favors own share of resources | Favors fair or mutually beneficial allocation |
| Restlessness | Values stability, tradition and order | Values autonomy, novelty and change |

Retain five short distinctions: Integrity is not Sharing; Curiosity is not
Restlessness; Presence is not leadership; Forbearance is not general kindness;
Caution is not simply the inverse of courage. Add concise model-facing pole fields
to the existing authoritative registry rather than maintaining another independent
trait list. Keep fuller construct metadata for documentation/public metadata.

Write the prompt positively around finding meaningful evidence. Preserve its
complete input contract (`target`, ordered `scenes`, canonical `scene`, and
`agent_scene_interpretation`), full JSON output, and source precedence. Keep module
flow docstrings, source-code prompt headers, definition order, and prompt version
synchronized. Do not expand this change into a rewrite of the other stage prompts.

## Deterministic calculation

The following is a proposed explicit interpretation of weighting both the mean
and smoothing. It changes numerical estimates and requires a new calculation
version; it is not equivalent to the current uniform formula.

For the chronologically ordered, deduplicated observations of one trait, let
`s_i` be +1 for high and -1 for low, `q_i` be 2 for canonical and 1 for
perspective, and `r_i = 0.9 ** (n - i - 1)` for zero-based index `i`.

```text
mean = sum(r_i * q_i * s_i) / sum(r_i * q_i)
evidence_mass = sum(q_i)
continuous_value = 5 + 4 * mean * evidence_mass / (evidence_mass + 4)
```

Keep the existing rounding away from 5 at ties and the 1–9 bounds. With no
observations, return unknown with a null point. Using un-decayed evidence mass
preserves the current separation between recency in the mean and accumulated
support in smoothing. All-perspective evidence reproduces the current numerical
formula; all-canonical evidence shrinks less toward 5.

`observation_count`, `high_count`, and `low_count` remain counts of independent
scene opportunities after the event grouping described below, never evidence mass.
For example, one canonical high yields a continuous value
of approximately 6.333; one perspective high yields 5.8. Both still have one
observation and provisional status. Opposing observations remain in the ledger;
supported estimates may describe mixed evidence near 5.

**Recommended initial support rule:** at least two independent scenes
with at least one `canonical` observation, or at least three independent scenes
when all observations are perspective-derived. Two perspective observations
remain provisional even though their evidence mass equals one canonical observation.
This is a proposed product threshold, not a psychological reliability claim.
Evaluate it explicitly; do not inherit today's `n >= 2` rule silently.

Deduplicate repeated records by agent + canonical scene + trait first, retaining
backend perspective provenance, then apply the event grouping below before scoring.
Refresh, retries, changed perspective IDs, or copied source groups cannot increase
support. Reject conflicting duplicate records rather than picking an arbitrary
polarity. Distinct scene IDs do not establish independent events. Do not introduce
LLM deduplication or treat similar prose as proof of a shared event.

## Steadiness and downstream use

### Revised construct and boundaries

Steadiness measures **how consistently a character expresses the same personality
dispositions across independent scenes**. It is observed cross-scene dispositional
consistency, a different construct from the old consistency under comparable
circumstances. Update authoritative metadata, UI explanations, synthesis, decision
context, and canonical documentation accordingly. Without comparing circumstances,
the estimator cannot distinguish unstable dispositions from stable context-sensitive
behavior. High or low Steadiness is not a moral judgment or a measure of calmness.

All calculation, support classification, and deduplication belong in the existing
deterministic reducer. The extraction LLM receives no Steadiness definition,
instructions, fields, or validation rules. Keep the Stage 3 prompt target at
2,500–4,000 characters and its existing eight-slot output.

### Independent observation opportunities

Before calculating Steadiness, resolve records into independent underlying event
groups, scoped to the agent and ontology. Scene IDs alone are insufficient:
different scenes may retell the same event. Reuse authoritative event/milestone
identity or explicit duplicate provenance where it actually identifies the same
observation opportunity. `DERIVED_FROM` identifies a source entity, not an event;
sharing a source or a milestone participant does not prove duplication.

The current embodiment query loads scene IDs, descriptions, dates, and source
anchors, but no authoritative event-equivalence mapping. Implementation must trace
available graph/import provenance and carry a backend-owned, revision-stable
`observation_group_id` (or equivalent mapping) through checkpoints and persistence.
Reuse an existing authoritative owner if one exists. If equivalence is not already
represented, add the smallest explicit backend mapping for reviewed duplicate
groups; do not claim scene IDs or content hashing solve paraphrased retellings.
Record unresolved suspected duplicates for review and exclude those records from
Steadiness repeat support until resolved. Report excluded coverage separately.
This is a rollout dependency, not a new extraction stage or model output field.

Within each group and trait, retain one strongest observation, with canonical
precedence. Agreeing retellings add neither mass nor repeat count. Conflicting
equal-strength duplicate interpretations require review and are excluded for that
group/trait; they are not evidence of inconsistency between independent events.
Retain raw records for audit. Fix group chronology to the underlying event's
established order, so a newly imported retelling cannot refresh recency. Do not
merge an entire partly overlapping scene with another without establishing that
the relevant trait observations concern the same event. Use this grouping for
directional support too, so duplicated stories cannot inflate the eight traits.

### Calculation and capped support

Proposed initial constants: retain the existing latest-12-observation window per
trait, require at least two independent groups for a trait to contribute, and cap
each trait's repeat-support mass at 4. These are engineering settings to validate
in evaluation, not empirically calibrated psychological parameters.

For each eligible trait `t`, take its latest `n_t` deduplicated observations in
event order. Use `q_i = 2` for canonical and `1` for perspective and
`r_i = 0.9 ** (n_t - i - 1)`. Compare polarities only within that trait:

```text
H_t = sum(r_i * q_i for high observations)
L_t = sum(r_i * q_i for low observations)
D_t = 2 * min(H_t, L_t) / (H_t + L_t)

c_t = min(4, sum(q_i for observations after the first in this trait window))
M = sum(c_t)
D = sum(c_t * D_t) / M
S = 5 + 4 * (1 - 2 * D) * M / (M + 4)
```

The first observation anchors comparison; only later independent opportunities
contribute repeat-support mass. The cap applies both to averaging disagreement
and to smoothing, so a long Caution series cannot dominate other repeated trait
series. Evidence weights affect polarity totals and repeat mass; recency affects
polarity totals. Keep the existing rounding away from 5 at ties and 1–9 bounds.
If `M = 0`, skip division and return the baseline below.

`M` is capped weighted repeat mass, not a count of scenes or confidence. Public
`observation_count` counts unique independent groups contributing to eligible
trait series, including their comparison anchors; a group supporting several
traits counts once. Preserve all contributing scene/perspective references in
audit provenance. Adjust the count/ID validator so multiple aliases of one event
cannot masquerade as independent observations. Repeated traits in the same event
may inform separate dimensions but cannot inflate the cross-event status threshold.

Five agreeing Caution observations give `D_t = 0` and contribute toward high
Steadiness. Alternating high/low Caution observations give disagreement approaching
1 and contribute toward low Steadiness. No calculation compares high Caution with
low Integrity. A single observation of Presence, Integrity, or Forbearance contributes
no repeat support, even alongside twenty Caution observations.

### Initialization, status, and overrides

| Evidence | Value and status |
| --- | --- |
| No eligible repeated independent observations (`M = 0`) | `point: 5`, `status: baseline`, zero contributing observations |
| Some eligible repeat support | Rounded `S`, `status: provisional` |
| Sufficient independent repeat support across multiple traits | Rounded `S`, `status: supported` |
| Administrator override | Manual value; retain the underlying baseline or inferred estimate |

Recommended initial supported threshold: at least two traits each observed in
three independent groups within their windows, and at least six unique independent
groups across eligible traits. Weighted mass alone cannot meet this threshold.
Perspective-only series can qualify after sufficient independent corroboration;
they are not upgraded by duplicate retellings. Validate this proposed threshold
alongside the cap and window before pilot acceptance.

`baseline` means a neutral computational prior, not measured moderate consistency.
It is valid only for Steadiness, requires point 5 and zero contributing observations,
and must be distinguishable from an inferred point 5. Preserve existing directional
status semantics. Update backend/SDK estimate validators, the Steadiness decision
projection, UI enum handling, and synthesis together. Baseline projects effective
value 5 with inference `availability: unavailable`, `inferred_value: null`, and a
reason explaining the absence of repeat evidence. Unresolved duplicates or missing
provenance must remain visible in diagnostics; missing/corrupt evidence for a
recorded inference is unavailable, not silently relabeled baseline.

For provisional/supported estimates, mark inference available only when the
versioned reducer reproduces the snapshot from its recorded evidence and grouping.
Manual overrides preserve the inferred estimate, count, and provenance; clearing
an override restores that inferred estimate or baseline as appropriate. Historical
old-policy revisions retain their original definition, Steadiness, and verification
calculation. New-policy revisions use the new construct even with legacy records,
provided event independence can be established.

Pass source labels through representative evidence and final synthesis so it can
describe perspective-derived provisional tendencies cautiously. Preserve existing
score/status admission for public `personality_traits`; do not promote a provisional
estimate by prose alone. Adjust source diversity selection in
`representative_evidence` without depending on contexts. Decision context keeps
effective ordinal values/statuses and separate baseline or verified inferred
Steadiness. Neither baseline nor manual values imply observed consistency.
Neither synthesis nor decisions become another extraction stage.

## Compatibility, persistence, and rollout

1. Introduce a new evidence-policy and calculation version. Version the profile
   policy explicitly so empty profiles and manual-only snapshots also identify the
   calculation. Keep dual readers for current and new versions; update server and
   SDK validators together. Do not silently reinterpret an old snapshot as weighted.
2. Persist source on new observations, map model `reason` to existing ledger
   `justification`, and retain scene/perspective/revision provenance. Keep
   `situation_type` as `unspecified` for new evidence to preserve the existing wire
   shape; it is absent from the new LLM contract. Legacy context fields remain
   readable. Derive weight from source in the backend rather than storing editable
   numbers. Keep `authored_disposition` excluded from scene scoring as today.
3. Legacy behavioral records lack source. Treat them as canonical only when
   consumed by the new-policy reducer, because their admission contract was
   canonical-only. Preserve their original policy and bytes. Historical revision
   reads dispatch by that snapshot's policy and reproduce the old formula/status.
   New snapshots may combine legacy canonical evidence and new labeled evidence.
4. A refresh that adopts the new policy may change scores, statuses, and inferred
   Steadiness from retained evidence, including its changed construct. Make this
   explicit in draft review. It does
   not retroactively extract perspective observations from processed scenes.
   Assess old scenes via frozen replay; a full rebuild is a separate explicit
   operation, with backups and existing lifecycle protections.
5. Bump the embodiment prompt version and include policy identity in checkpoint
   compatibility. Reject incompatible cached analyses; retain compatible reads
   of older drafts without accepting mismatched computations.
6. Revision-owned JSON should permit these additions without a SQL schema migration
   or new graph node type. Event-equivalence mapping may require an additive graph
   property or mapping; verify existing provenance before choosing storage.
   Verify every load/write, acceptance, deletion, partial
   retry, and paginated-history path before concluding no migration is needed.
7. New evidence fields/profile versions affect strict older SDK clients. Prepare
   dual-version SDK/frontend support before emitting new responses; disclose the
   upgrade requirement in endpoint documentation. Authorization and ontology
   isolation stay governed by the existing service/router checks.
8. Export the pilot's profile, ledger, revisions, and portrait before acceptance.
   Deploy compatible API/workers together. Code rollback alone cannot restore
   weighted snapshots or accepted portraits, and old code may reject new records;
   preserve dual readers or restore the saved data with the matching release.

## Implementation sequence and verification

### 1. Freeze baseline and specify the new policy

Capture the complete current prompt, provider schema, fixture bytes, and model
settings. Use `character_trait_evaluation.py` and `trait_discrimination.json` as
the starting evaluation path. Retain old expectations as a versioned baseline;
review the few deliberately changed perspective-only expectations separately.
Extend output comparison to include source as well as trait and polarity.

### 2. Change extraction as one contract

Update prompt fragments, private schemas, provider shaping, sanitization, and
backend mapping together. Replace context-specialized models with one shared
candidate shape while preserving eight required nullable slots. Malformed
individual candidates become null through the existing sanitizer; missing slots,
extra envelope keys, and wrong scene counts retain bounded recovery behavior.
Keep normal call count, configured model role, scene chunking, and retry budgets.

### 3. Change scoring and versioned reads

Implement weights, support thresholds, event-group deduplication, profile
policy dispatch, historical reproduction, and baseline/evolving new-policy Steadiness
in the existing services. Update synthesis, diagnostics, persistence, SDK models,
and checkpoint handling together. Keep manual edit behavior and revision ownership.

### 4. Validate contracts and evaluate extraction

Extend existing tests for both poles and sources, all-null/all-eight scenes,
canonical/perspective agreement and conflict, perspective preference without an
outward action, invented behavior, wrong-character attribution, and prior-portrait
echoes. Verify source enums and required nullable fields in runtime and provider
schemas, candidate isolation, correction exhaustion, and ordered scene binding.

Verify exact weighted calculations, recency, rounding symmetry, one strong versus
two weak observations, two-weak provisional/three-weak supported status, mixed
evidence, replay across source groups and perspective IDs, old/new snapshot reads,
paginated history without future evidence, missing ledgers, manual override clearing,
Steadiness policy separation, drafts/checkpoints, administrator access, and ontology
isolation. Extend the current suites rather than creating parallel test scaffolding.

Add Steadiness cases for no repeats/baseline, inferred 5 versus baseline 5,
agreeing versus alternating poles, per-trait grouping, 12-observation windows,
cap saturation, recency changes, unequal source weights, and supported thresholds
using unique event groups rather than summed per-trait counts. Cover a dominant
Caution series with both single and repeated observations of other traits; twenty
observations of one trait cannot alone produce supported status. Verify duplicate
event records with distinct scene IDs, paraphrased retellings with explicit shared
provenance, conflicting duplicates, partial overlaps, unresolved duplicates,
stable group chronology, versioned group mappings, and missing historical grouping.
Check manual/baseline transitions, snapshot reproduction, SDK/UI enum handling,
and unchanged extraction call count, schema, and prompt size after this revision.

Planned focused commands, run from `shrecknet/`:

```sh
pytest tests/test_character_traits.py tests/test_character_personality_evolution.py tests/test_character_embodiment_validation.py tests/test_character_trait_persistence.py
pytest tests/test_character_embodiment.py tests/test_character_embodiment_jobs.py tests/test_character_embodiment_debug_artifacts.py tests/test_character_agents.py tests/test_character_agent_decision_making.py tests/test_character_retrieval_isolation.py
```

Run affected SDK tests from `python_sdk/`, then the Shrecknet package suite when
practical. Use the existing offline evaluator before opt-in live comparisons.
Record unsupported/missed observations, source errors, grounding errors, token
estimates, retries, failures, and call counts. Review Johnny only with his actual
canonical scenes and perspectives; do not infer success from the supplied example.

Measure `len(TRAIT_INTERPRETATION_PROMPT)` for baseline and candidate. Target
2,500–4,000 fixed characters, but report measured results rather than assume the
supplied baseline size. Separately measure serialized provider-schema size and
full request/token estimates: the fixed prompt is only part of the request.
Prioritize a complete self-contained contract if the target needs adjustment.
No minimum profile density or balanced pole distribution is an acceptance target.

### 5. Documentation and pilot delivery

Update [Dispositional Traits](Dispositional%20Traits.md),
[CharacterAgent](CharacterAgent.md),
[CharacterAgent endpoints](CharacterAgent%20-%20Endpoints.md), SDK contracts/docs,
and affected runnable examples with the implemented policy. Include formula,
source precedence, support thresholds, revised Steadiness definition and limitation,
baseline status, capped repeat support, event deduplication, public version
compatibility, historical dispatch, refresh limitations, and rollback requirements.
Update the documentation index when page purposes change. Add a changelog entry
according to the established release process.

Deliver a reviewed pilot draft after focused checks and source-aware evaluation.
Accepting it remains the existing explicit draft operation. Planning introduces
no production writes, inference calls, data migration, or automatic regeneration.

## Implementation results

The fixed prompt measures 3,784 characters versus the frozen v43 baseline's
20,779. Stage 3 supplies no Steadiness instructions or context enums and retains
one normal call per chunk. The initial cap/window/status thresholds above are
implemented. Revision profiles additionally snapshot `observation_groups`,
`observation_group_positions`, and `unresolved_observation_ids`, allowing retained legacy evidence to reproduce new
estimates without changing old ledger rows. Scene APIs carry reviewed grouping
and unresolved flags; normalized exact-copy description hashes are a conservative
fallback, not paraphrase detection. Paraphrased retellings require reviewed grouping.

No SQL migration or automatic rebuild is introduced. Deploy matching readers and
workers; preserve pilot exports before acceptance. Tests exercise structural and
deterministic behavior, not live semantic extraction quality.

Verification: 320 affected backend tests and all 35 SDK tests passed using the
repository virtual environment. `git diff --check` and added local documentation
links passed. The full Shrecknet suite stops during collection on existing missing
imports: `architect_v2`, SQL `OntologyInstance`, and `_normalize_scene_ranges`.
An additional scene-boundary validation expectation fails with both this change
and the original HEAD service code; it is unrelated and retained unchanged.
Live LLM replay, real Johnny evaluation, production graph deployment, and draft
acceptance were not run.
