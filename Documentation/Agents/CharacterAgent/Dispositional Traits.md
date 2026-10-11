# CharacterAgent dispositional traits

CharacterAgent uses eight directional personality points and a separate
STEADINESS point. The runtime contract is `app/schemas/character_traits.py`;
`GET /character-agents/trait-definitions` supplies names, poles, construct
boundaries, diagnostic situations, and the 1–9 scale. These are narrative
engineering estimates, not population-calibrated personality measurements.

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
| STEADINESS | Behavioural consistency / spread of the behavioural distribution | Behaves similarly when genuinely comparable circumstances recur. | Broader variation around the same dispositional centres. | Narrower variation; dispositions predict behavior more tightly in comparable situations. | Repeated comparable behavior only | A second moment, not a situation predictor, morality, calmness, or model temperature. Requires repeated comparable behavior. |

<!-- END GENERATED TRAIT DEFINITIONS -->

## Profile and graph representation

`CharacterAgent.trait_profile` and each `CharacterIdentityRevision.trait_profile`
store versioned JSON with `dispositional_traits` and separate `steadiness`.
Every estimate contains only `point` (strict integer 1–9 or null), `status`
(`unknown|provisional|supported|manual`), `observation_count`, and
`observation_ids`. Unknown means null, never an implicit point 5. Point 5 with
opposing evidence means a mixed or context-dependent record; binary observations
cannot prove an inherently average disposition. The eight directional slots are
always present. `inferred_traits` and `overrides` preserve the underlying estimate
when an administrator sets a manual point. Clearing an override restores the
current inference; actor and reason are recorded in `CharacterIdentityChange`.

Every `observation_id` is an owned `ScenePerspective.id`, never `Scene.id` or a
trait-evidence record ID. `observation_count` equals the number of distinct IDs
used in that estimate. `ScenePerspective-[:PROJECTS_ON]->Scene` retains canonical
scene provenance. `CharacterIdentityRevision.trait_evidence` stores compact
observations; `CharacterIdentityChange` records profile transitions. The backend
preassigns perspective IDs in the draft before aggregation and writes the same
IDs on acceptance. Revision entries hold their assigned revision ID. A referenced
perspective cannot be deleted without regenerating the derived trait history.
The active policy is `point-evidence-v1`. Revision evidence must cite a
`ScenePerspective.id` created in its source batch and that perspective's
canonical scene; the backend rejects mismatched or duplicate references before
writing the timeline. Old z-formatted payloads and profiles
are rejected by the new runtime schema.

## Extraction and evidence

Traits answer **How does this character tend to behave?** Admit evidence of a
behavioral tendency through the existing construct definitions. A temporary
reaction or isolated outcome is insufficient by itself; a diagnostic voluntary
choice can supply evidence without establishing a settled trait. Identity facts
and established capabilities alone do not establish trait observations. This
admission wording does not change the constructs, scales, or reducers. For the
boundaries with aspects and goals, see the [CharacterAgent profile admission
contract](CharacterAgent.md#evidence-grounded-embodiment).

```mermaid
flowchart TD
    A[Persistent identity_description] --> B[Stage 0: load or generate narrative grounding]
    C[Canonical scene] --> D[Stage 1: grounded subjective perspective]
    C --> F[Stage 3: categorical trait interpretation]
    D --> E[Backend scene binding and perspective ID]
    T[Authoritative trait meanings] --> F
    E --> G[Stage 2: psychological enrichment]
    E --> F
    F --> H[Backend validates, binds and deduplicates trait evidence]
    H --> I
    I --> J[Directional and STEADINESS reducers]
    J --> K[Point profile and audited changes]
```

`identity_description` contains narrative summaries and trait descriptions,
without points or polarity. It is loaded once per embodiment job, or generated
from the entity and current profile if absent, and supplied to perspective and
psychological stages. Stage 3 receives `identity_summary` and
`psychological_summary` as context, but not `personality_traits`. Trait
observations must be grounded in canonical scenes, using the authoritative
`IDENTITY_TRAIT_DESCRIPTION_CONTRACT` meanings. After all
scenes are processed, the identity description is refreshed once from the final
current state. It never contributes authored trait evidence to the numerical
profile.

Stage 1 binds one subjective perspective to each canonical scene. The backend
validates scene order and evidence references, then assigns the perspective ID.
Stage 2 handles emotions, beliefs, aspects, and goals in parallel with Stage 3.
Each source is one revision boundary. Its scenes are analyzed in chunks of at
most five, using one shared source-start identity. All perspective chunks finish
before psychological enrichment and trait interpretation run in parallel for
each chunk. The backend merges chunk results in scene order and performs one
deterministic source update. A source with `n` chunks normally makes `3n` LLM
calls. Identity-description generation adds a call if the field is absent and
one refresh call per embodiment job; repairs and provider retries
may add calls.
Stage 3 sees each canonical scene and its validated Stage 1 `source_type` and
`perspective`, plus the two narrative summaries as context and the authoritative
trait meanings. The canonical scene establishes what happened, the character's
actions and words, circumstances, and available alternatives. The perspective
helps interpret the character's knowledge, motives, beliefs, and perceived options;
its subjective claims do not establish canonical behavior. A candidate needs
scene-supported behavior or an explicitly expressed value from this character.
If perspective and scene conflict, canonical facts govern what happened; omit
the candidate if the trait signal cannot be grounded. Mere witnessing or hearing
about another person's behavior does not make it the character's behavior. Stage 3
receives no prior `personality_traits`, and summaries cannot establish trait evidence.
Each internal model-facing scene result has exactly the eight required directional
trait keys. Each slot is null or an object containing exactly `polarity`, `context`,
and a nonblank `justification` of at most 360 characters. All eight dimensions are
considered; zero through eight observations may be emitted, with no three-trait cap.
Null means no supported observation, not missing comparison metadata.

`context` is one trait-specific enum generated by `trait_context_values` from
`TRAIT_BY_KEY[trait].diagnostic_situations`, `friend|enemy|other`, and
`ordinary|high_stakes`, plus `unspecified`. Both the provider JSON Schema and Python
validate the same complete values. There are no independently nullable context
fields or cross-field completeness rule. The backend maps `context` directly to
persisted `situation_type`, then supplies the trait from its slot key, `perspective_id`,
evidence ID, source/revision ownership, chronological position, and policy version.
Existing persistence, API, SDK, directional scoring, and STEADINESS formats remain
unchanged. Specified contexts can continue accumulating STEADINESS evidence;
`unspecified` contributes only to the directional estimate. Select it whenever any
comparison detail cannot be grounded, without discarding an otherwise diagnostic action.

For example, a scene result may contain:

```json
{
  "integrity": null,
  "caution": {"polarity": "low", "context": "uncertain_threat:other:high_stakes",
              "justification": "Voluntarily declines offered protection and explicitly dismisses concern about the danger."},
  "presence": null,
  "forbearance": null,
  "diligence": null,
  "curiosity": null,
  "sharing": null,
  "restlessness": null
}
```

The complete model response wraps ordered results in `scene_trait_interpretations`.
The LLM never supplies points, confidence, identifiers, or a STEADINESS observation.
`TRAIT_DEFINITION_CONTRACT` includes definitions, both poles, and `boundary_notes`
from the authoritative registry. `TRAIT_EVIDENCE_CONTRACT` specifies the full output
and admission rules. One diagnostic voluntary action or explicit value statement
can supply an observation without establishing a settled trait. An alternative
need not be explicitly narrated; the model must not invent alternatives, motives,
or freedom from constraints. Known compulsion, immediate necessity, impossible
obligations, and ambiguous attribution can make actions non-diagnostic. Temporary
feelings or outcomes alone are insufficient, while a diagnostic response in one
scene can count without proving an enduring tendency.

Malformed non-null observations are individually replaced with null and logged
with scene and trait; other valid slots survive. Missing required slots, extra
keys, wrong scene count, and malformed envelopes use the bounded validation retry
budget. Duplicate JSON keys remain rejected. Native structured output is requested;
the existing explicit unsupported-format fallback retains backend validation.

Do not extract from compelled or ambiguous action, group-only or witnessed
behavior, an unavailable alternative, or a behavior that does not reveal the
construct. The narrative `identity_description` never creates trait evidence
or a provisional point. The backend admits at most
one observation per trait and perspective; retries and duplicate descriptions
cannot multiply support. Opposing observations are retained. All-null scene
results are normal.

`situation_type` is either `unspecified` or
`<diagnostic>:<relationship>:<stakes>`. The diagnostic value must belong to the
trait registry. Relationship is `friend|enemy|other`; stakes is
`ordinary|high_stakes`. Use `unspecified` if circumstances are insufficient for
comparison. It still contributes to the directional trait but cannot affect
STEADINESS. Distinct friend and enemy contexts are never collapsed into one
consistency group.

## Directional calculation

For one trait, let eligible observations be ordered oldest to newest. The newest
observation of that trait has `k=0`; older ones have `k=1,2,...`. High polarity
has `x=+1`, low polarity has `x=-1`. Defaults are recency `rho=0.9` and smoothing
`lambda=4`:

```text
w_i       = rho^k_i
mu        = sum(w_i * x_i) / sum(w_i)
raw       = 5 + 4 * mu * n / (n + lambda)
point     = clamp(round_half_away_from_5(raw), 1, 9)
```

`n` is the number of distinct eligible perspective IDs for that trait. Recency
sets direction; actual observation count controls movement from point 5. Ties
at an exact half point round away from 5, symmetrically at both poles. With
only same-pole scene observations, 1, 5, 10, and 28 observations yield points
6, 7, 8, and 9 (or 4, 3, 2, and 1 for low). A long run of new opposite choices
can reverse an older personality. With `rho=1`, this becomes
`5 + 4(H-L)/(H+L+4)` before rounding. This scoring rule is versioned policy,
not a validated psychological scale.

Only scene perspective observations contribute to the directional calculation.
With no eligible scene observations, the trait remains `unknown`; one eligible
perspective remains `provisional`; two or more make a directional estimate
`supported`. Legacy `authored_disposition` records do not contribute to points.

## STEADINESS calculation

STEADINESS measures variation *within* comparable contexts, not morality,
calmness, or the average directional trait. Group observations by exact
`(trait, situation_type)`; exclude `unspecified`. Within each group take at most
the newest 12 observations. A group qualifies with at least three independent
perspectives. If none qualify, STEADINESS is unknown. For each group compute
recency-weighted high and low masses `H_g` and `L_g`:

```text
d_g              = 2 * min(H_g, L_g) / (H_g + L_g)
D                = sum((H_g + L_g) * d_g) / sum(H_g + L_g)
raw              = 5 + 4 * (1 - 2 * D) * m / (m + 4)
steadiness_point = clamp(round_half_away_from_5(raw), 1, 9)
```

`m` is the number of distinct contributing perspectives. Three identical
comparable observations yield provisional point 7, not an apparently certain 9.
At least six observations across two qualifying context groups make the estimate
`supported`. A character who always forgives friends and always retaliates
against enemies can therefore be consistent. Binary polarity and a compact
context catalog lose nuance; use `unspecified` when comparison is not grounded.
With enough comparable evidence, the smoothed consistency scale can reach both
points 1 and 9; a three-observation group cannot produce either extreme.
STEADINESS never controls sampling temperature.

## Use, operations, and cutover

Decision making uses the persisted `identity_description` as its personality
source; it does not hydrate numerical trait points, statuses, poles, or
STEADINESS into the deliberation prompt. Trait estimates remain available for
profile display and evidence audit.
The administrator evidence endpoint is
`GET /character-agents/{agent_id}/trait-evidence`; its records have
`perspective_id`, `polarity`, `situation_type`, `justification`, and backend
provenance. Each evidence record is stored on its `CharacterIdentityRevision`
with `revision_id`, `source_group_id`, batch ownership, and the originating
perspective plus `scene:<scene_id>` reference. The revision is also connected to
its source entity through `CONSOLIDATED_FROM`. This maps every retained trait
observation back to the source and scene that produced it. STEADINESS is derived
from those same contextual observations and appears as its own trait change and
revision estimate; it does not require a separate model-generated score.
Revisions allow reconstruction and audit. Manual edits use
`{"point": 7, "reason": "..."}`; `point: null` clears an override.

Deployment of this breaking contract requires a backup and regeneration of
traits from authored identity and source scenes/perspectives. Old
`expression_z` records cannot be faithfully converted by rounding. Old-format
revisions may be archived outside the active path but must not supply current
aggregation, API responses, edits, or query context. A character without source
material has unknown traits until regeneration. For an existing old-format
agent, export the graph backup, delete that CharacterAgent through the normal
administrator endpoint, start a new embodiment draft for its canonical entity,
and accept the regenerated agent. Do not append new point revisions to its old
z history. Restore the backup for rollback.
The [redesign plan](Point-Based%20Traits%20Redesign%20Plan.md) records acceptance
criteria and the full verification matrix; the [endpoint page](CharacterAgent%20-%20Endpoints.md)
records HTTP details.


## Diagnostic extraction and narrative synthesis

Scene extraction applies three gates to each candidate: whose behavior, what makes
it diagnostic beyond competence or task participation, and which authoritative
pole it supports. The existing justification identifies the character's canonical
action and its diagnostic significance. Investigation, preparation, and coordination
do not automatically produce curiosity, diligence, or presence. All eight
dimensions compare both authoritative poles and construct boundaries,
with paired diagnostic examples and confounds. Both poles are admissible; no
balance quota or artificial character differences are imposed.
Ambiguous, compelled, or non-diagnostic behavior yields no observation. The
required eight-slot schema permits all supported traits from one scene.
Grounding validators, source/chunk call structure, and deterministic reducers remain.

Final identity synthesis uses actual contributing behavioral examples, scores,
statuses, high/low distributions, accepted current psychology, and bounded recent
perspectives. Previous identity text provides continuity only. See
[CharacterAgent](CharacterAgent.md#chronological-identity-revisions) for the input
budget, score interpretation, and authority rules.

## Continuous revision history

Revision reads add `trait_history`, a read-only map for the eight directional
traits. Each entry contains `inferred_continuous_value`, `high_count`, `low_count`,
`observation_count`, `availability`, `reason`, and
`calculation_version: "recency-points-v1"`. The calculation shares the existing
recency-weighted mean and `n / (n + 4)` smoothing with integer consolidation;
no formula, status, rounding, or persisted profile changes occur. Counts are
unweighted and are not confidence intervals. Unknown with complete empty evidence
has a null estimate and zero counts. Missing or inconsistent contributing evidence
has `availability: "unavailable"` and null numerical details.

Each revision uses only ledger records introduced through that revision and only
its recorded contributing perspective IDs. Earlier pages of the ledger are included
when revision pagination starts partway through history. Evidence added later is
excluded. Manual overrides keep the effective integer in `trait_profile` while
`trait_history` describes underlying inference. Steadiness remains a separate
integer calculation and is absent from this map. Public revision reads expose
these aggregate details under existing visibility rules; raw evidence remains
administrator-only. The numerical character sheet and downstream integer analyses
retain their existing behavior.

## Offline diagnostics and rollout

Run from `shrecknet/`:

```sh
python -m app.services.character_trait_analysis ledger.json report.json
```

Input is `{"characters":[{"character_id":"stable-id","name":"optional",
"extraction_version":"optional prompt version","processed_scene_ids":["scene-id"],
"evidence":[/* exported TraitEvidence objects */]}]}`. Export private ledgers only
through existing administrator-authorized reads. `processed_scene_ids` is optional
and must come from processing records, not inferred absence in the evidence ledger.
The report contains per-character and aggregate high/low counts by trait, total
behavioral observations, separate authored evidence counts, duplicate counts,
scene coverage, and shared-scene justification comparisons. Identical duplicate
records are counted once; conflicting or duplicate perspective evidence is rejected.
Missing processing information is unknown, distinct from a processed scene with
zero observations. Counts expose patterns; a high/low imbalance alone does not
establish a bias.

The prompt version is `character-embodiment-v41-eight-slot-trait-context`;
synthesis context identifies `evidence-synthesis-v1`. Definition and evidence-policy
versions remain unchanged. Existing checkpoints include the prompt version in their
fingerprint and cannot reuse outputs from the old prompt. Deploy API and workers
together, then SDK/frontend consumers. No database migration is required. Decimal
history is available from existing ledgers without inference calls. Existing
observations and narratives are not rewritten on deployment: review a draft for a selected character before acceptance. Existing-agent drafts
retain processed scene evidence and append newly eligible scenes; they do not
retroactively re-extract old observations merely because the prompt changed.
Use offline replay to inspect revised extraction on exported canonical scenes
before planning a full rebuild. Preserve a backup/export of the original ledger
and revisions for any full regeneration; no automatic rebuild or deletion occurs
on deployment. Frozen adventure analysis results remain unchanged. Re-extraction
requires LLM stages; reporting and decimal history do not. Rollback restores earlier prompts/UI while
retaining compatible integer profiles and ledgers.


### Frozen extraction evaluation

`shrecknet/tests/fixtures/trait_discrimination.json` contains 34 synthetic cases:
both poles of all eight traits, three character-specific views of a shared artifact
scene, five original non-diagnostic/attribution cases, and ten additional cases
covering single actions without explicitly enumerated alternatives, confounds,
perspective-only behavior, and eight independently supported traits in one scene. These are authored reference
expectations requiring review, not actual evidence for Ernst, Johnny, or Johan.
The replay command uses the configured extraction model and existing validator;
it supplies frozen subjective perspectives and never writes profiles or scenes.

```sh
python -m app.services.character_trait_evaluation \
  tests/fixtures/trait_discrimination.json current-report.json --live
python -m app.services.character_trait_evaluation \
  tests/fixtures/trait_discrimination.json baseline-report.json --live \
  --prompt-file /path/to/previous-complete-extraction-prompt.txt
```

The baseline prompt file must include its full authoritative trait contract and
use the current eight-slot response schema. A six-field historical prompt cannot
be replayed through the new parser; compare its captured observations with
`--outputs`, or run that baseline with its matching code version.
Use identical fixture bytes and configured model/settings for each comparison;
LLM variability means one run is a review aid, not a statistically established
quality claim. Reports record fixture/prompt hashes, model settings, LLM calls,
estimated tokens, unsupported/missed candidates overall and separately by pole, scenes without
observations, and actual justifications.
Inspect action attribution, diagnostic significance, low/high direction, and
unsupported motivations manually. Do not require equal pole distributions or
artificial differences between characters.

For existing captured outputs, score without network or LLM calls:

```sh
python -m app.services.character_trait_evaluation \
  tests/fixtures/trait_discrimination.json scored-report.json --outputs outputs.json
```

`outputs.json` is an array with exactly one `{ "case_id": "...", "observations":
[{"trait":"...","polarity":"high|low","justification":"..."}] }` per fixture
case. Empty observations are valid. Model replay requires a reachable shreckLLM
service; failed inference does not publish a successful report.
