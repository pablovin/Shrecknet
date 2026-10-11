# CharacterAgent dispositional traits

CharacterAgent uses eight directional personality points and separate STEADINESS.
The authoritative contracts live in `app/schemas/character_traits.py` and the
reducers in `app/services/character_trait_service.py`. These are narrative
engineering estimates, not calibrated psychological measurements.

## Constructs

<!-- BEGIN GENERATED TRAIT DEFINITIONS -->

| Slot | Construct | Definition | Left pole | Right pole | Diagnostic situations | Boundaries |
| --- | --- | --- | --- | --- | --- | --- |
| INTEGRITY | Honesty-Humility — HEXACO | Will not gain at someone else's expense even when the gain is free, safe, and untraceable. | Takes available advantage; accepts profitable exploitation and self-serving deception. | Refuses unfair advantage or untraceable exploitation, even at personal cost. | exploitation, self_serving_deception | Not taking unfairly, not giving. Generosity, compassion, friendliness, and forgiveness alone are not evidence. |
| CAUTION | Emotionality — HEXACO | Registers danger early and seeks security or guarantees before exposure or uncertain reliance. | Physically fearless, little worry or need for reassurance; less dependent and sentimental. | Threat-sensitive, anxious, seeks guarantees, protection and support; strong attachments and fear of loss. | uncertain_threat, uncertain_dependence, reliance_without_guarantees | Includes attachment and dependence, not just risk-taking. High values often seek guarantees or withdraw. Low values are not inherently virtuous courage. |
| PRESENCE | eXtraversion — HEXACO | Speaks first, stands at the front, and seeks company and social visibility. | Stays at the edge, avoids attention, and is drained by company. | Takes the floor, joins and approaches others; socially bold and energized by company. | social_visibility, social_approach, voluntary_contact | Not dominance, leadership, warmth, kindness, or cooperation. Forbidden speech is not low presence. |
| FORBEARANCE | Agreeableness — HEXACO | Absorbs insult, obstruction, or betrayal rather than retaliating. | Retaliates, holds grudges, becomes angry quickly, and refuses to bend. | Forgives, compromises, remains mild, lets provocations pass and defuses conflict. | provocation, betrayal, obstruction, retaliation | Response after being wronged, not general compassion or integrity. An honest character can consistently retaliate. |
| DILIGENCE | Conscientiousness — HEXACO | Finishes properly what nobody is checking. | Cuts corners, improvises, abandons tasks, acts impulsively, or is disorganized. | Organizes, persists, deliberates, fulfills obligations, and completes work thoroughly without supervision. | unattended_duty, delayed_payoff, cutting_corners, persistence | Not competence: careful work that fails can be strong high-pole evidence. |
| CURIOSITY | Openness — HEXACO | Goes and looks at the strange thing. | Prefers familiar routes and finds unfamiliar or unconventional things off-putting. | Investigates, seeks information, explores unfamiliar phenomena, and enjoys unconventional ideas. | novelty, exploration, puzzle, unknown_information | Not intelligence or successful understanding. Known fatal danger confounds avoidance. One investigation does not establish restlessness or low caution. |
| SHARING | Social Value Orientation — SVO | When jointly controlled resources are divided, moves the allocation toward the other party, including costless giving. | Keeps the larger share, maximizes own allocation, or values being ahead. | Divides evenly or in the other party's favor; may maximize joint benefit. | resource_allocation, spoils, rewards | Giving/allocating, not refraining from exploitation. Do not automatically infer integrity. |
| RESTLESSNESS | Openness-to-Change vs Conservation — Schwartz values | Values the new and self-chosen over the settled, traditional, and safe. | Values security, order, tradition, conformity, and stability. | Values novelty, autonomy, stimulation, self-direction, and change for its own sake. | value_conflict, recurring_value_preference | A motivational value orientation, not simple exploration. Requires explicit value choices or recurring motivated preferences. |
| STEADINESS | Behavioural consistency / spread of the behavioural distribution | Expresses the same personality dispositions consistently across independent scenes. | Broader variation around the same dispositional centres. | Narrower variation; dispositions express the same direction across independent scenes. | Repeated independent scenes | Observed cross-scene consistency cannot distinguish unstable dispositions from stable context-sensitive behavior. Baseline 5 is a computational prior, not observed consistency. |

<!-- END GENERATED TRAIT DEFINITIONS -->

## Profile and graph representation

New profiles use `dispositions-v5-neutral-prior`; existing
`dispositions-v3-points-perspectives` and `dispositions-v4-weighted-scenes`
snapshots remain readable with their original calculations and defaults. Every profile
contains eight `dispositional_traits`, separate `steadiness`, `inferred_traits`,
and audited `overrides`. Estimates contain `point` (integer 1–9 or null), `status`,
`observation_count`, and `observation_ids`. Directional statuses are
`unknown|baseline|provisional|supported|manual`; historical unknown requires null.
All nine new effective estimates start at 5/`baseline`, requiring zero observations
and empty observation IDs. This computational prior is distinct from an observed
5 with provisional/supported status. Directional baseline is valid in v5 profiles
and inferred states; v3/v4 retain their original restrictions. No artificial
observations are inserted. Authored text and summaries do not seed points.

Observation IDs remain owned `ScenePerspective.id` references. After event grouping,
one representative perspective identifies each contributing independent opportunity;
a group supporting several traits counts once for Steadiness. Raw ledger records
retain every scene/perspective reference. `observation_groups` maps perspective IDs
to event groups in the profile; `unresolved_observation_ids` snapshots exclusions.
`observation_group_positions` snapshots the earliest recorded position across
traits, so stronger retellings retain event recency in historical reads.
These preserve grouping when new revisions use retained legacy evidence, without
rewriting earlier ledger rows. Old snapshots ignore these new mapping defaults.

`CharacterIdentityRevision.trait_evidence` stores revision-owned JSON. New records
use `point-evidence-v2-weighted-scenes`, with `source` (`canonical|perspective`),
`observation_group_id`, `independence_status` (`independent|unresolved`), existing
`justification`, provenance, and `situation_type: unspecified`. Legacy records
remain readable, defaulting source to canonical under the new reducer because the
old admission policy was canonical-only. `authored_disposition` remains unscored.

The backend validates source-batch perspective ownership and the perspective's
canonical scene before timeline writes. Raw evidence is administrator-only;
existing visibility and ontology isolation apply. Referenced perspectives cannot
be deleted without regeneration. Old z-formatted profiles remain invalid.

## Extraction and evidence

Stage 1 produces grounded subjective perspectives. Stage 2 psychology and Stage 3
trait extraction then run in parallel for each chunk; source consolidation follows
all chunks. The worker retains the existing model roles, chunking, retry budget,
source revision boundaries, and one normal Stage 3 call per chunk. Identity
synthesis follows each source's trait aggregation and aspect/goal
consolidation, refreshes all three identity fields, and completes before the next
source starts. It uses scored evidence and accepted psychology; it never extracts points.

Stage 3 receives `target.alias`, context-only `identity_summary` and
`psychological_summary`, and ordered scenes with `position`, canonical
`scene.name`/`scene.description`, and validated `agent_scene_interpretation`
(`source_type`, `perspective`). It receives no prior `personality_traits`.

Each scene returns all eight nullable trait slots. A non-null slot contains exactly
`polarity: low|high`, `source: canonical|perspective`, and nonblank `reason` of at
most 360 characters. The wrapper is `scene_trait_interpretations`, with exactly one
result per input scene in the same order. For example:

```json
{"scene_trait_interpretations":[{
  "integrity":null,
  "caution":{"polarity":"high","source":"perspective",
             "reason":"Johnny prefers protection against the rite's influence."},
  "presence":null,"forbearance":null,"diligence":null,"curiosity":null,
  "sharing":null,"restlessness":null
}]}
```

Canonical means directly documented diagnostic behavior or explicit words by this
character, weight 2. Perspective means a character-specific scene-anchored
preference, motivation, or tendency in the supplied subjective interpretation,
weight 1. The backend assigns weights and maps `reason` to ledger `justification`.
These are reliability conventions, not scientific ratios.

Evaluate all dimensions independently and select the strongest source per trait
per scene. Canonical wins agreement or disagreement between channels; agreeing
channels produce one strong observation. Opposing poles within the strongest
channel without a defensible dominant direction yield null. Perspective evidence
can express a preference without an outward action; it cannot invent actions,
events, alternatives, or preferences, contradict facts, borrow another character's
behavior, or repeat a prior portrait as evidence. Generic feelings, compelled
behavior, routine participation, and competence alone are insufficient.

The compact definitions come from the authoritative registry and retain five
construct distinctions. No context enums, stakes, relationship classifications,
numerical strengths, confidence, identifiers, or Steadiness enter the Stage 3
output. Runtime and provider schemas enforce the same compact shape. Malformed
candidates become null individually with logging; missing slots, extra envelope
keys, and wrong scene counts retain bounded correction behavior. Structural
validation cannot prove psychological or semantic grounding.

## Independent event grouping

A canonical scene is the first deduplication boundary, not proof of event
independence. Scene create/update/read payloads support `observation_group_id`
(nullable nonblank string, at most 255 characters) and `independence_status`
(`independent|unresolved`, default independent). Assign the same reviewed group ID
to different scene records retelling the same event, within the same ontology.
Use unresolved for suspected duplicates or unresolved observation attribution;
those event/trait groups contribute no directional or Steadiness support.

Explicit groups take precedence. Otherwise a normalized full-description digest
conservatively groups exact copied descriptions; empty descriptions fall back to
scene identity. A digest detects exact copies, not paraphrased retellings.
Paraphrases and partial overlaps require reviewed explicit event grouping. Sharing
a `DERIVED_FROM` source or participants does not establish event equivalence.
There is no semantic deduplication model call or claim of automatic paraphrase
recognition. Separate actual events with identical descriptions need distinct
explicit group IDs to avoid conservative merging. Do not group whole overlapping
scenes when their trait observations concern different events.

Within a group/trait, retain the strongest source once; conflicting equal-strength
observations exclude that group/trait. Preserve raw records for audit. Group recency
uses its earliest recorded chronological position, so later imports of retellings
cannot refresh it. Comparisons remain local to the character/ontology. Retained
legacy observations resolve against current scene provenance for a new computation;
the resulting profile snapshots the mapping. Changing provenance for processed
scenes changes their digest and requires explicit regeneration, rather than
silently rewriting accepted history.

## Directional calculation

For deduplicated chronological observations of one trait, let `s_i` be +1/high or
-1/low, `q_i` be 2/canonical or 1/perspective, and
`r_i = 0.9 ** (n - i - 1)` for zero-based index `i`:

```text
mean = sum(r_i * q_i * s_i) / sum(r_i * q_i)
mass = sum(q_i)
continuous_value = 5 + 4 * mean * mass / (mass + 6)
```

Round away from 5 at ties and bound integers to 1–9. Empty contributing evidence
returns 5/`baseline` before division. Excluded ledger rows remain preserved but
supply no support. Mass is the un-decayed sum of source weights; recency adjusts
direction. Counts remain independent opportunities, never mass. One canonical
high yields 6.0; one perspective high yields 5.571429. Both are provisional
with count 1. Supported requires two independent observations with some canonical
evidence, or three independent perspective-only observations. Opposing evidence
may support a mixed estimate near 5. Thresholds are engineering choices.

| Canonical observations, oldest first | Continuous value | Rounded point |
| --- | ---: | ---: |
| None | 5.000000 | 5, baseline |
| 1 High | 6.000000 | 6 |
| 2 High | 6.600000 | 7 |
| 3 High | 7.000000 | 7 |
| 3 High, then 1 Low | 5.956424 | 6 |
| 2 Low, then 1 High | 4.476015 | 4 |
| 10 High | 8.076923 | 8 |

Recalculate from real contributing evidence and the neutral prior whenever the
ledger changes; never add a delta to the prior rounded score. Smoothing controls
displacement, not support status or calibrated action probabilities. Manual
values remain effective; clearing an override restores inference or baseline.
The extraction stages and per-source refresh order are unchanged.

## STEADINESS calculation

Steadiness retains smoothing 4 and its separate calculation in both v4 and v5.
Its baseline remains 5 without observed consistency. New-policy Steadiness means observed cross-scene dispositional consistency.
It cannot distinguish unstable dispositions from stable context-sensitive behavior;
it is not equivalent to the legacy comparable-circumstances construct.

For each trait take its latest 12 independent observations. A trait needs at least
two observations to contribute. Compute high/low totals using the same recency
and source weights, then disagreement within that trait:

```text
H_t = sum(r_i * q_i for high observations)
L_t = sum(r_i * q_i for low observations)
D_t = 2 * min(H_t, L_t) / (H_t + L_t)
c_t = min(4, sum(q_i for observations after the first in the trait window))
M = sum(c_t)
D = sum(c_t * D_t) / M
S = 5 + 4 * (1 - 2 * D) * M / (M + 4)
```

Use the same rounding and bounds. The first observation anchors comparison;
subsequent independent opportunities provide repeat mass. The per-trait cap of 4
applies to disagreement averaging and smoothing. Never compare high Caution with
low Integrity. A singleton trait contributes no repeat support. Twenty observations
of one trait alone cannot make Steadiness supported or overwhelm all other series.

With no repeat support, skip division and return point 5/status baseline/count 0.
With some repeat support, calculate provisional Steadiness. Supported requires at
least two traits with three independent observations each and six unique event
groups across the eligible series. Multiple traits from one event cannot inflate
that cross-event threshold. Perspective-only series can qualify after independent
corroboration. An inferred rounded 5 retains its provisional/supported status.
Manual overrides preserve inference; clearing them restores inference or baseline.

## Continuous revision history

`trait_history` is a read-only map for the eight directional traits. It contains
`inferred_continuous_value`, unweighted `high_count`/`low_count`/`observation_count`,
`availability`, `reason`, and `calculation_version`. New snapshots use
`recency-weighted-scenes-v3-neutral-prior` for v5, using smoothing 6; v4 retains
`recency-weighted-scenes-v2` and smoothing 4. v3 retains `recency-points-v1`, uniform
weights and `n/(n+4)` smoothing, original statuses, and old Steadiness verification.
New directional baseline details have continuous value 5, available computational
inference, zero counts, and a reason identifying the prior. This does not mean
behavior was observed. Historical unknown details remain null.
History dispatches by the snapshot profile version, uses only its contributing
IDs and revision-owned grouping, includes earlier ledger pages, and excludes future
revisions. Missing or non-reproducing evidence yields unavailable/null details.
Effective manual integers remain distinct from underlying continuous inference.

## Diagnostic extraction and narrative synthesis

Synthesis receives bounded source-labeled evidence examples and effective scores.
Perspective-derived evidence remains tentative interpretation. Source diversity
replaces artificial context diversity. New synthesis must return exactly one
nonblank description for every directional trait, including baseline and historical
unknown entries. Duplicate or missing keys fail generation validation and use the
existing retry budget. Older sparse portraits remain readable through the public
`IdentityDescription` contract. Prior portraits remain continuity-only.

A score of 5 does not require a generic description. Personalize it through the
character's established identity, psychology, goals, relationships, and stakes.
For baseline Integrity: “Johnny's search for his maker has not yet revealed whether
he would accept an unfair advantage.” With actual opposing evidence, an observed
5 could instead describe rejection of exploitation among companions but acceptance
of advantages while pursuing his maker. Never infer the latter from baseline.
Equal scores can have different character-grounded meanings; do not invent motives,
moderation, equal probabilities, or observed mixed behavior to make prose unique.
Sparse context may legitimately produce similar neutral descriptions.
Steadiness can qualify `psychological_summary`, including its lack of observed
consistency at baseline, but never becomes a ninth directional description.
Steadiness is separate and never a ninth narrative trait or model-temperature input.
Its synthesis/decision `calculation_version` identifies `cross-scene-steadiness-v2`
or legacy `comparable-steadiness-v1`, so the model can distinguish the constructs.

Steadiness baseline projects value 5, status baseline, unavailable inference, null
`inferred_value`, and an explanatory reason. It permits no observed-consistency
claim. Provisional/supported consistency is available only when the versioned
reducer reproduces the snapshot. Missing/corrupt inference is unavailable, not
silently relabeled baseline. Manual values remain effective without proving
observed consistency. The typed decision context and prompts distinguish baseline
from inferred 5 and preserve the same single deliberation call.

## Use, operations, and cutover

The prompt version is `character-embodiment-v48-emotion-length-tolerance`; synthesis uses
`evidence-synthesis-v4-neutral-prior`. Prompt fingerprints invalidate old
checkpoints. New snapshots store grouping; prior snapshot/ledger bytes are not
rewritten. Optional scene provenance is additive and needs no SQL schema migration
or new graph node type. Scene provenance edits on processed data require explicit
regeneration. Existing-agent refreshes retain processed scenes; a changed prompt
does not backfill perspective evidence. An ordinary source update adopts the new
scoring policy for retained evidence, potentially changing points and Steadiness.

Deploy matching API/workers and upgraded SDK/frontend consumers together: strict
older clients may reject the new profile version, baseline enum, and added fields.
No frontend implementation lives in this repository. Review a pilot draft before
acceptance; preserve exported profiles, ledgers, revisions, grouping, and portraits.
Old code may reject new records. Rollback requires dual-version readers or restoring
the saved data with its matching release; code rollback does not undo accepted
portraits or weighted snapshots. No automatic regeneration or migration runs.

## Offline diagnostics and rollout

From `shrecknet/`, use the existing diagnostics without model calls:

```sh
python -m app.services.character_trait_analysis ledger.json report.json
```

Input contains `characters` with character IDs, optional names/extraction versions,
exported `evidence`, and optional `processed_scene_ids`. Export private evidence
through administrator-authorized reads. Diagnostics include raw source/pole counts,
independent trait-observation counts, unresolved records, duplicate-record counts,
processing coverage, and shared-scene reasons. Raw count and independent support
are intentionally separate. Counts do not prove psychological validity.

### Frozen extraction evaluation

`trait_discrimination.json` retains the original 34 authored reference cases.
`trait_weighted_sources.json` adds 21 source-aware cases: both poles of every
perspective-derived trait, canonical agreement/conflict, invented actions, generic
feelings, and prior-portrait echoes. These are synthetic review expectations, not
real character evidence. The evaluator checks optional `expected_sources` as well
as trait/polarity, and records source errors and actual reasons.

Run offline with captured outputs or explicitly opt into model replay:

```sh
python -m app.services.character_trait_evaluation tests/fixtures/trait_weighted_sources.json report.json --outputs outputs.json
python -m app.services.character_trait_evaluation tests/fixtures/trait_weighted_sources.json report.json --live
```

Captured outputs contain exactly one `case_id` and `observations` array per case;
observations contain `trait`, `polarity`, `source`, and ledger `justification`.
Replay uses the production extractor and configured model without graph/profile
writes. Semantic grounding, preferences, source precedence, and construct boundaries
require review beyond structural tests. Review real Johnny material only with his
actual canonical scenes and perspectives; neither profile density nor balanced
pole distributions are acceptance targets.

The frozen v43 prompt is `trait_interpretation_baseline_v43.txt`. Its old context
output cannot be replayed through the compact parser: run it with matching v43
code or compare captured old outputs. Use identical fixture bytes and model settings
for valid comparisons. Fixed prompt characters decreased from **20,779 to 3,784**;
this excludes provider schema and scene inputs. Record full request tokens, latency,
failures, and repair calls separately; no live quality or cost improvement is claimed
from prompt length alone. Live comparisons and pilot acceptance remain operational
checks, not automatically executed by tests.

## Behavioral identity evaluation

`tests/fixtures/identity_synthesis.json` contains 32 synthetic frozen synthesis
inputs: both poles of all eight directional traits, unknown/sparse/mixed evidence,
comparable-context consistency, equal diligence with different authored motives,
supported and unsupported interactions, temporary state, manual/inferred conflict,
a missing ledger, competing goals, a non-diagnostic factual question, constrained
responses at both Forbearance poles, and an older forgiving portrait with an
effective manual retaliation score.
They are authored review cases, not evidence about real
characters. `identity_synthesis_baseline.txt` preserves the complete v41 synthesis
prompt and registry contract for comparison. `decision_making_baseline.txt` preserves
the complete previous portrait-only decision prompt. Fixture version is
`behavioral-identity-v2-decision-situations`; earlier portrait reports must be
regenerated against the identical expanded fixture bytes before comparison.

Run from `shrecknet/` with a reachable configured shreckLLM service:

```sh
python -m app.services.character_identity_evaluation synthesis \
  tests/fixtures/identity_synthesis.json baseline-identity.json --live \
  --prompt-file tests/fixtures/identity_synthesis_baseline.txt
python -m app.services.character_identity_evaluation synthesis \
  tests/fixtures/identity_synthesis.json candidate-identity.json --live
python -m app.services.character_identity_evaluation decisions \
  tests/fixtures/identity_synthesis.json decision-comparison.json --live \
  --baseline baseline-identity.json --candidate candidate-identity.json \
  --trials 20 --temperature 0.7
```

Live synthesis uses the production request builder, validator, and retry budget.
The baseline changes only its instance-local prompt; both versions receive
identical frozen input. Live decisions use the production deliberation job with
baseline, candidate, and neutral arms. Each case fixes options, knowledge,
capability, and opportunity. Identity arms have identical empty memory lists;
all situational knowledge is in the shared context. This isolates the portrait
without changing normal gameplay's subjective memory retrieval. Trial order
alternates. Neutral mode receives no identity or memories. No graph operations,
profile writes, or production retrieval changes occur. At 32 cases and 20 trials,
each decision comparison requests 1,920 deliberations before any repair calls; use an
explicit smaller `--trials` value for a smoke check.

Reports retain validated outputs, fixture/prompt hashes, configured model settings,
temperatures, request counts including replacements/repair, synthesis token
estimates, and portrait-report hashes for decisions. Decision report version 2
also records per-trial model/reasoning settings, context inclusion, latency,
request-message hashes, input-token estimates (characters divided by four, not
provider billing), and failures. Output/hidden-reasoning token totals are not
available in this runner. Seeds are not supplied and are recorded as null.
Decision inference/validation failures remain explicit `error` trial records and
contribute to `failed_trials`, rather than disappearing from action counts.
Successful trials carry `result`; failed trials carry `error` with `type` and
`message` and no result. Synthesis failures still abort. Interrupted runs are not
resumable, and existing report files are not removed on an aborted run. Baseline
and candidate portrait reports must match the identical fixture hash.

### Isolated decision comparisons

`--comparison portraits` is the default and changes only the portrait. Other
comparisons hold the candidate portrait fixed for both identity arms:

| Comparison | Baseline arm | Candidate arm |
| --- | --- | --- |
| `prompt` | Supplied previous complete decision prompt; no numeric context | Current decision prompt; no numeric context |
| `context` | Current prompt; portrait only | Same prompt/portrait plus compact scores and Steadiness |
| `reasoning` | Same current prompt, portrait, numeric context and model; reasoning off | Identical inputs/model; reasoning on |

Each comparison retains the generic neutral arm without character context and
alternates trial order. `--deliberation-target /path/to/target.json` accepts an
explicit `LLMModelTarget` object (`provider`, `name`, `reasoning`) for this run;
it never updates production settings. Use a provider/model supporting reasoning
for that comparison. Prompt overrides are local to the evaluation client and
never alter production prompt globals or JSON-repair instructions.

```sh
python -m app.services.character_identity_evaluation decisions \
  tests/fixtures/identity_synthesis.json prompt-comparison.json --live \
  --baseline baseline-identity.json --candidate candidate-identity.json \
  --comparison prompt --decision-baseline-prompt tests/fixtures/decision_making_baseline.txt \
  --trials 20
python -m app.services.character_identity_evaluation decisions \
  tests/fixtures/identity_synthesis.json context-comparison.json --live \
  --baseline baseline-identity.json --candidate candidate-identity.json \
  --comparison context --trials 20
python -m app.services.character_identity_evaluation decisions \
  tests/fixtures/identity_synthesis.json reasoning-comparison.json --live \
  --baseline baseline-identity.json --candidate candidate-identity.json \
  --comparison reasoning --deliberation-target /path/to/target.json --trials 20
```

Captured-output review accepts the same `--comparison` label with `--outputs`;
it records that declared comparison and the captured-input hash, without
inventing model or prompt provenance. Failed-trial records may be included.
Predeclare quality, failure-rate, latency, and cost criteria before reviewing
outputs. The portrait-only context ablation is available only within the evaluation
client; production identity mode always supplies numerical context. Use these
comparisons to assess its effect and evaluate reasoning independently. Current tests establish transport and boundary
behavior; live comparisons and a reviewed production pilot remain pending.

Captured-output review is offline and makes no inference calls:

```sh
python -m app.services.character_identity_evaluation synthesis \
  tests/fixtures/identity_synthesis.json reviewed-identity.json --outputs portraits.json
python -m app.services.character_identity_evaluation decisions \
  tests/fixtures/identity_synthesis.json reviewed-decisions.json \
  --outputs decisions.json --trials 20
```

`portraits.json` is an array of exactly one `{"case_id":"...",
"identity_description":{"identity_summary":"...","psychological_summary":"...",
"personality_traits":[]}}` per case. `decisions.json` has exactly one
`{"case_id":"...","variant":"baseline|candidate|neutral","trial":0,
"result":{"type":"json","content":{"action":"one fixture option"},
"decision_basis":"concise public explanation"}}` per case, arm, and zero-based
trial. Reports validate structures and summarize action counts; they do not
automatically score prose or claim improvement. Captured-output provenance records
the input hash and mode, not unverified model settings.

Blindly review evidence fidelity, pole fidelity, supported conditional specificity,
unsupported causes/exceptions, and repetition. Set acceptance criteria and trial
budget before viewing comparisons. Compare repeated action distributions and
character fit with uncertainty; identical choices may be appropriate, and a single
generation cannot establish an improvement. Extraction evaluation above measures
a separate stage. Live narrative comparison, decision trials, and a reviewed
end-to-end pilot remain operational checks; fixture and mocked tests establish
contracts, not scientific validity.

The synthesis update requires no database migration, trait recalculation, or
memory re-embedding and adds no normal production LLM calls. Deploy API and workers
together. Existing portraits and ledgers remain unchanged until an explicit
embodiment refresh; review a draft before replacing a pilot portrait and preserve
an export. Prompt versions invalidate incompatible checkpoints. Rollback restores
future generation behavior but does not restore accepted portraits automatically;
restore saved portraits through an authorized maintenance path if needed.

## Neutral-prior rollout and compatibility

New agents and explicit profile recalculation use v5. Existing profiles, revisions,
and portraits are unchanged on read. Processing new evidence upgrades the current
profile using its full retained ledger, potentially changing several points at
once; historical revisions retain their recorded policy. Explicit regeneration
can refresh an existing character under the new policy. Evidence admission and
`point-evidence-v2-weighted-scenes` are unchanged. No schema migration or automatic
backfill is required.

Deploy matching backend/workers and SDK readers. Older SDKs may reject v5 or
baseline directional estimates. Drain active jobs before rollout; checkpoint
version 4 and updated synthesis/prompt versions reject incompatible intermediate
work. Accepted historical drafts remain readable. Rollback requires a reader
compatible with persisted v5 profiles or an appropriate pre-upgrade backup;
relabeling v5 JSON as an old version is invalid.

See the [neutral-prior implementation record](Neutral%20Trait%20Prior%20and%20Conservative%20Scoring%20Plan.md)
for verification and policy decisions. Frozen offline prompt overrides retain the
historical sparse output schema; production synthesis enforces all eight entries.

For live or captured-output review of equal-baseline characters, use
`shrecknet/tests/fixtures/neutral_prior_identity_synthesis.json` with the existing
`app.services.character_identity_evaluation` CLI. Johnny and Cwenhild share the
same neutral profile but have different established goals and situations. Review
for character-grounded relevance and restraint about unobserved tendencies;
uniqueness and psychological fidelity are human judgments, not keyword scores.
