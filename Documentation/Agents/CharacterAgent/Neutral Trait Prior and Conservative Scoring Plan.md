# Neutral Trait Prior and Conservative Scoring Plan

Status: implemented. The design below records the agreed scope. The current
contract is maintained in [Dispositional Traits](Dispositional%20Traits.md).

## Objective and scope

Give new CharacterAgents a complete effective profile: all eight directional
traits and separate Steadiness start at point 5, status `baseline`, with zero
contributing observations. Make directional inference more conservative by
increasing its smoothing from 4 to 6 while preserving canonical/perspective
weights, chronological recency, event deduplication, and provenance.

Keep the source-bundle extraction and evolution pipeline unchanged. Its existing
per-source reducers and identity refresh will consume the updated profile contract.
No synthetic observations, authored-text trait seeding, new model calls, new
configuration setting, or replacement scoring framework are proposed.

This plan evaluates the supplied proposal against repository behavior. The PDF
mentioned in the request was not supplied and has not been reviewed.

## Current behavior and implications

- `schemas/character_traits.py` defaults directional estimates to `unknown`/null
  and Steadiness to `baseline`/5. `TraitEstimate` already supports baseline, but
  `TraitProfile` explicitly forbids it for directional effective/inferred values.
- `services/character_trait_service.py` uses weighted chronological observations
  and smoothing 4. `_directional()` returns unknown when there is no contributing
  evidence. Recalculation already starts from evidence rather than incrementing
  the previous rounded point.
- One shared `SMOOTHING` constant currently affects current and historical
  directional and Steadiness calculations. A global edit would alter old revision
  reconstruction and unintentionally change Steadiness inference.
- `trait_history_details()` verifies snapshot estimates against their contributing
  evidence. Baseline projection must reproduce 5 with zero observations without
  treating the prior as an observed mean or marking it corrupt.
- Synthesis and decision context carry statuses as well as values. Prompts need
  explicit baseline semantics; merely replacing null with 5 would invite invented
  balanced behavior. Scores on this scale are not calibrated action probabilities.
- Backend and SDK validators both prohibit directional baseline. The SDK's profile
  version is a closed literal. Both packages must change together.

## Proposed profile and representation contract

| State | Effective point | Status | Interpretation |
| --- | --- | --- | --- |
| No contributing directional evidence | 5 | `baseline` | Computational prior; no established tendency toward either pole |
| Contributing evidence below support threshold | Calculated 1–9 | `provisional` | Tentative directional inference, including when rounded to 5 |
| Evidence meets support threshold | Calculated 1–9 | `supported` | Supported inference, including balanced or conflicting evidence near 5 |
| Administrator override | Explicit point | `manual` | Effective authored value; inferred state and evidence remain separate |
| Historical unknown estimate | null | `unknown` | Retained historical contract; do not relabel it on read |

Baseline invariants: point exactly 5, count 0, empty `observation_ids`, no fabricated
evidence or behavioral examples. An empty or entirely excluded contributing set
returns baseline even when raw ledger rows exist; preserve those rows and their
exclusion diagnostics. A baseline must not imply that a character has no history.

New directional defaults and no-evidence recalculation use baseline. Clearing a
manual override restores the latest inferred state, including baseline when no
evidence contributes. Manual overrides remain effective and retain provenance.

Retain the existing status thresholds in this focused change: directional
`supported` requires at least two independent contributing events with at least
one canonical observation, or at least three perspective-only events. Smoothing
controls displacement from 5; status describes the existing support policy and
does not promise confidence or near-extreme behavior. Any stricter threshold is
a separate explicit policy decision, not an implicit consequence of smoothing.

## Directional calculation and checked examples

For each deduplicated contributing observation, `d_i` is +1/high or -1/low,
`q_i` is 2/canonical or 1/perspective, and `r_i = 0.9 ** (n - i - 1)`:

```text
M = sum(q_i)
mean = sum(r_i * q_i * d_i) / sum(r_i * q_i)
continuous_value = 5 + 4 * mean * M / (M + 6)
```

For an empty contributing set, return baseline 5 before division. Preserve current
rounding to the nearest integer, ties away from 5, bounded to 1–9. Mass `M` is
un-decayed source-weighted evidence mass, not the observation count and not the
sum of recency-adjusted weights. Recency affects direction; retained evidence
continues to contribute mass as in the current policy.

These examples were calculated with independent canonical observations in the
listed chronological order, so each contributes mass 2:

| Observations, oldest first | Continuous value | Effective point |
| --- | ---: | ---: |
| None | 5.000000 | 5, baseline |
| 1 High | 6.000000 | 6 |
| 2 High | 6.600000 | 7 |
| 3 High | 7.000000 | 7 |
| 3 High, then 1 Low | 5.956424 | 6 |
| 2 Low, then 1 High | 4.476015 | 4 |
| 10 High | 8.076923 | 8 |

The proposed “2 Low, 1 High: about 5” does not round to 5 under the existing
weights and chronology. Tests and documentation must use the actual formula
rather than force that illustrative result. Different ordering gives different
mixed-evidence results. Rounded extremes remain reachable with enough consistent
evidence; smoothing reduces early displacement without guaranteeing that every
future update is a small step when provenance or retained evidence changes.

## Steadiness decision

Recommended scope: keep Steadiness's separate repeat-consistency calculation,
repeat caps, support thresholds, and smoothing 4 unchanged; retain baseline 5
until repeat support exists. Introduce a directional smoothing constant of 6
instead of changing the shared constant globally. This isolates the requested
directional formula change and preserves existing consistency semantics.

If smoothing 6 is also desired for Steadiness, implement that as an explicit
versioned consistency-policy change with separate expected values and tests.
Do not accidentally alter it through shared constants. In either case, baseline
Steadiness conveys no observed consistency, stability, or instability.

## Psychological synthesis and decisions

Regenerate all eight directional descriptions with explicit neutral wording for
baseline entries. Example: “No established tendency toward either exploiting or
rejecting unfair advantages.” Do not claim equal probabilities, alternating
behavior, moderation, indecision, or a known mixed disposition from baseline 5.
An evidence-supported 5 can describe observed opposing tendencies when its
contributing evidence supports that account.

Keep `IdentityDescription`'s three fields. Keep `personality_traits` dedicated to
the eight directional constructs, with exactly one description for each in newly
generated identities. Represent Steadiness separately in the existing profile
and synthesis/decision input, and convey its neutral or observed consistency
meaning in `psychological_summary`. This represents all nine estimates without
turning Steadiness into a ninth directional trait or changing extraction slots.
Continue reading legacy identities with shorter description lists.

Update synthesis and decision prompts, their human-readable input/output
contracts, deterministic output checks, and offline evaluation cases together.
Baseline traits cannot manufacture motives or constrain action as an observed
disposition; canonical identity, established psychology, goals, knowledge, and
the decision situation remain available. Neither summaries nor baseline values
create numerical evidence. Supported contradictions may correct prior summaries.

## Versioning and compatibility

Introduce a new scoring/profile version and directional calculation version;
select historical formulas by recorded version. Preserve v3 and v4 parsing,
defaults, smoothing 4, unknown/null semantics, and historical replay exactly.
Do not simply add a new `SPEC_VERSION` and let old v4 records fall through an
“everything except v3 is current” branch. Audit all such branches, including
history verification, evidence snapshots, synthesis, decision context, and API
calculation-version labels. Evidence admission/policy version can remain unchanged
because extraction and provenance rules are unchanged.

New agents and explicit recomputation use the new version. Existing accepted
agents and stored revisions remain unchanged on read. Applying new evidence may
upgrade the current profile from its real accumulated ledger; document that this
can change several scores at once as a policy upgrade. Historical revisions must
retain their original values. Offer existing explicit regeneration for users
wanting the new neutral portrait without waiting for new scenes; no automatic
backfill or silent portrait rewrite is proposed.

Version identity synthesis and affected prompts/checkpoint contracts so old cached
descriptions and finalized source results cannot resume under new scoring rules.
New profile version fingerprints also invalidate incompatible downstream work.
Deploy matching API, workers, and SDK support. Older SDK releases may reject the
new profile version or directional baseline; document that client upgrade need.

## Implementation sequence

1. Add focused regression tests for baseline defaults, no-evidence recalculation,
   checked scores, support statuses, manual edits, and frozen v3/v4 reconstruction.
2. Update backend and SDK profile contracts with version-specific defaults and
   validators. Retain strict baseline invariants for effective and inferred states.
3. Update the authoritative reducer with explicit versioned directional policies;
   pin historical constants and isolate Steadiness. Reuse existing evidence
   grouping, recency, snapshot provenance, and rounding owners.
4. Update history details and synthesis/decision projections: baseline is a valid
   computational value with no empirical evidence. Distinguish its lack of
   observations from unavailable/corrupt evidence and supported balanced behavior.
5. Update prompt contracts and generation validators for complete eight-trait
   descriptions, neutral baseline language, and separate Steadiness treatment.
   Preserve stage order, model routing, chunking, and per-source refresh timing.
6. Update canonical docs, API examples, SDK docs/models/examples, metadata, and
   offline evaluation fixtures. Document the score-policy upgrade and rollback.

## Verification and completion gates

- Every new effective estimate is 5/baseline/count 0 with no evidence insertions.
- Canonical and perspective observations retain weights 2 and 1; recency remains
  0.9. Excluded, unresolved, conflicting retellings, and duplicate events retain
  current handling. Cross-character and ontology isolation remain unchanged.
- Check high/low symmetry, chronology, balanced supported 5 versus baseline 5,
  provisional rounded 5, status thresholds, mixed sources, substantial consistent
  evidence, and recomputation after removal or provenance correction.
- Preserve manual override values/reasons and inferred baselines; clearing restores
  inference. Authored identity text and generated descriptions never seed points.
- Frozen v3/v4 profiles reproduce original continuous/rounded scores, Steadiness,
  history availability, and statuses. New no-evidence details do not fail replay.
- Newly generated portraits cover all eight directional keys; legacy portraits
  remain readable. Baseline fixtures carry no behavioral examples or unsupported
  likelihood/consistency claims. Live semantic quality is evaluated separately
  from deterministic schema checks; do not pretend arbitrary prose can be proven
  neutral by a substring test.
- Two-source regression proves the first source's new-version state and summaries
  reach the second, with unchanged extraction stages. Retry, supersession,
  checkpoint invalidation, draft acceptance, and atomic append tests remain green.
- Backend/API and SDK round-trip tests cover new baseline profiles and all retained
  versions, including invalid baseline points/counts and legacy nulls.

Run narrow reducer/schema tests first from `shrecknet/`, then affected
CharacterAgent/configuration suites; run SDK tests from `python_sdk/`. Record exact
commands and outcomes. A live comparison may assess portrait usefulness but is
not required to establish arithmetic correctness. Verification results are recorded in the implementation record below.

## Documentation and operations

Canonical updates: `Dispositional Traits.md`, `CharacterAgent.md`, CharacterAgent
endpoint contracts/examples, query personality context, and SDK trait/profile
documentation. Link this plan from `Documentation/README.md`. Update model/prompt
execution documentation where identity output requirements change; pipeline order
does not change. No root README change is necessary without new setup requirements.

No database schema migration is proposed; the stored JSON contract gains a new
version. Drain active generation jobs before rollout and restart matching API and
workers. Incompatible intermediate checkpoints must be regenerated. Preserve
completed historical drafts and revisions. Rollback must account for new-version
profiles that older code cannot parse: retain a compatible reader or restore an
appropriate pre-upgrade data backup; do not downgrade by relabeling JSON versions.

Related: [Dispositional Traits](Dispositional%20Traits.md),
[CharacterAgent](CharacterAgent.md),
[Source Bundle Evolution Plan](Source%20Bundle%20Evolution%20Plan.md).

## Implementation record

- Profile version: `dispositions-v5-neutral-prior`; historical v3/v4 remain readable
  and reproduce their original scores/defaults. Directional calculation version:
  `recency-weighted-scenes-v3-neutral-prior`.
- Directional smoothing is 6; historical directional and all Steadiness smoothing
  remain 4. Evidence policy, extraction, stage order, and per-source evolution are
  unchanged. No artificial observations are inserted.
- New identity generation requires all eight directional descriptions. At 5,
  meaning remains character-driven through established identity/psychology/goals;
  baseline does not imply an observed preference, balance, or equal probabilities.
  Sparse grounding permits similar descriptions instead of invented uniqueness.
- Synthesis version: `evidence-synthesis-v4-neutral-prior`; embodiment prompt:
  `character-embodiment-v48-emotion-length-tolerance`; decision prompt:
  `character-decision-v4-neutral-prior`; draft checkpoint version: 4.
- Explicit offline prompt overrides retain the historical sparse portrait schema
  to support frozen comparisons. Production generation uses the complete schema.
- Verification: 439 CharacterAgent/configuration tests and all 36 SDK tests passed.
  Python compilation and `git diff --check` passed. Live model quality and production
  database integration were not run. The equal-baseline fixtures are provided for
  explicit human review through the existing evaluation CLI.

Commands (run from the respective package directories):

```bash
# shrecknet/
SHRECKNET_MEDIA_ROOT=/tmp/shrecknet-test-media ../.venv/bin/python -m pytest tests/test_character_*.py tests/test_embodiment_validation_config.py -q -rA
# python_sdk/
../.venv/bin/python -m pytest -q
```

The media override uses a writable directory because this environment's default
`/app` media path is unavailable. Existing framework deprecation warnings remain.
