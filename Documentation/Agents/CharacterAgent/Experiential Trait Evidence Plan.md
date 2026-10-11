# Experiential Trait Evidence Plan

**Status:** proposed; no runtime or public contract changes implemented.

## Goal and scope

Make Stage 3 use grounded subjective experience to describe tentative dispositions,
while preserving the existing behavioral evidence, integer trait profile, and
pipeline. Deliver this as a small experiment first, followed by an additive
production change only if reviewed results justify it.

Success means more useful, traceable characterization where source material
supports it. It does not mean filling all eight traits or making existing scores
less sparse. A trait can remain numerically unknown while having a provisional
psychological hypothesis.

This proposal responds to the supplied Johnny examples. His full fifteen-scene
input and outputs have not been inspected; the quoted perspectives alone cannot
establish source grounding. The referenced psychological PDF was not supplied
with the feedback, so alignment with that document remains a review task.

## Existing owners and constraints

- `jobs/character_agent/embody_agent.py::_interpret_traits_batch` already receives
  canonical scenes and validated Stage 1 perspectives. Stage 3 runs in parallel
  with Stage 2 psychology. No additional normal LLM stage is needed.
- `embody_agent_prompts.py::TRAIT_INTERPRETATION_PROMPT` currently requires a
  canonical diagnostic action, explicit value, or demonstrated response. It
  explicitly excludes perspective-only feelings from observations.
- `trait_evidence_contract.py` and the private Stage 3 schemas define eight
  required nullable behavioral slots. Preserve these and add a separate optional
  experimental output collection rather than replacing their meaning.
- `schemas/character_traits.py::TraitObservation` currently distinguishes
  `behavior` and `authored_disposition`. Keep that scored-ledger contract intact
  for the first release; experiential candidates belong to a separate typed
  contract, not a new kind silently admitted to that ledger.
- `services/character_trait_service.py` owns grounding and deterministic scores.
  `directional_items` and `steadiness_estimate` consume behavioral evidence.
  Existing record IDs identify source group, perspective, and trait; placing two
  evidence channels there would also collide without redesign.
- `services/character_trait_evaluation.py`, existing extraction fixtures, and
  `test_character_personality_evolution.py` provide the starting evaluation path.
  Extend these owners instead of creating another extraction pipeline.

The normal proposed flow remains: Stage 1 perspectives -> parallel Stage 2
psychology and Stage 3 extraction -> existing source consolidation and behavioral
scoring -> final identity synthesis. Stage 3 additionally returns experiential
candidates; a backend reducer handles their provenance and recurrence separately.
Stage 4 aspect/goal consolidation does not become another trait classifier.

## Evidence admission

| Channel | Admission rule | Initial consequence |
| --- | --- | --- |
| Behavioral | Existing diagnostic canonical action, explicit value, or demonstrated response | Existing scoring and history |
| Source-supported experiential | Canonical material explicitly establishes this character's particular feeling, belief, preference, or interpretation, and it discriminates a trait pole | Provisional hypothesis; no numerical contribution |
| Generated interpretation | Perspective supplies a plausible internal reaction that canonical material does not establish | Review-only hypothesis; no independent evidence count or score |
| Non-diagnostic | Generic fear, task participation, identity repetition, ambiguous attribution, or no construct-specific signal | Omit |

A source-supported internal state need not include an outward action. However,
an event that could plausibly cause fear does not establish that the character
felt fear. Grounding requires evidence for the internal claim itself, not merely
for its surrounding event. Repeated generated language cannot upgrade that claim.

An explicit canonical statement may already qualify as behavioral evidence under
today's contract. Preserve that admission and link any experiential interpretation
to the same underlying event; it supplies no second independent observation.

## Delivery sequence

### 1. Freeze the baseline and reference cases

Export Johnny's canonical scenes, perspectives, extraction outputs, and existing
profile using authorized reads. Freeze their exact bytes and prompt/model settings
for comparison. Supplement the existing fixtures with both poles, canonical
subjective states without actions, generated-only thoughts, repeated retellings,
role-driven investigation, forced danger, contradictory evidence, and comparable
versus incomparable contexts.

Review expected classifications before running the candidate. Preserve all existing
behavioral expectations; richer output is measured separately from score coverage.

**Deliverable:** reviewed fixtures and an immutable baseline report.

### 2. Extend Stage 3 in offline evaluation

Keep the eight behavioral slots and add a bounded `experiential_candidates`
collection to the same response. Start with at most one candidate per trait per
scene. A scene can contain both channels when each is justified.

Define the complete Pydantic and human-readable prompt contracts together. Each
candidate should contain `trait`, `polarity`, `psychological_signal`,
`grounding` (`source_supported` or `generated_interpretation`), `source_excerpt`,
`perspective_excerpt` (nullable), and `justification`. The justification explains
the construct-specific significance and relevant situational confound. Require a
nonempty canonical excerpt for `source_supported`; generated interpretations cite
their perspective text and cannot masquerade as canonical evidence. Specify text
bounds and forbid model-generated IDs, confidence numbers, scores, and Steadiness.

Bind character, source, scene, perspective, and run/version identifiers in the
backend using the existing ordered scene mapping. Validate excerpts against the
correct supplied text and reject cross-scene references. An exact excerpt proves
traceability, not psychological correctness; semantic admission still requires
review. Drop malformed candidates individually using the established recovery
pattern, without discarding valid behavioral slots.

Update prompt headers, execution flow, schema shaping, sanitization, and prompt
version together. Exercise this contract through offline replay before enabling
production persistence or feeding the output into identity synthesis.

**Deliverable:** side-by-side behavioral and experiential reports, with raw
justifications, provenance, token use, retries, and failures.

### 3. Consolidate recurrence without numerical scoring

Implement the reducer beside the existing trait service. Keep its inputs and
outputs separate from `TraitEvidence`, `TraitProfile`, and `TraitHistoryEstimate`.

- Replaying a candidate is idempotent. Changing its text under a new prompt version
  supersedes that candidate rather than introducing another independent event.
- Use canonical scene identity rather than generated perspective IDs to prevent
  refreshes and source-group copies from increasing support.
- Different scenes are not automatically independent: linked retellings of one
  event form one support group. Reuse available canonical provenance; when it is
  insufficient, flag independence as unresolved rather than counting it as proven.
  Do not merge unrelated events merely because their language is similar.
- Preserve opposing poles and context. Describe mixed evidence explicitly instead
  of averaging it into a psychological score.
- One source-supported event permits a tentative signal. At least two reviewed
  distinct support groups permit a recurring hypothesis. This is a conservative
  product rule for the experiment, not a validated psychological threshold.
- Generated interpretations remain review-only regardless of repetition. They
  never increase the source-supported recurrence count.

Both experiential categories remain excluded from 1–9 values, behavioral counts,
recency weighting, manual overrides, continuous history, and Steadiness. Add direct
regression assertions for each invariant.

**Deliverable:** deterministic, auditable provisional summaries with distinct
source-supported and generated-only support.

### 4. Add persistence and presentation after the evaluation gate

Use an additive revision-owned JSON property for the separate experiential
channel, following existing revision persistence patterns. Missing data on older
revisions means “not evaluated,” not “evaluated with no candidates.” Carry the
channel through analysis, draft/checkpoint, acceptance, revision ownership, and
deletion paths together. Do not write it into the existing scored ledger.

Start with administrator review under existing authorization and ontology
isolation rules. Before exposing any new API fields, update server schemas, SDK
models/resources, API documentation, and consumer tests in the same change.
Existing generated thoughts should remain restricted diagnostic material.

If the pilot succeeds, supply only source-supported provisional summaries to the
existing final synthesis call. They may qualify `psychological_summary` with
explicit tentative language. Keep score-backed `personality_traits` admission
unchanged. Do not feed generated-only interpretations back into production identity
or later extraction as evidence. Keep provenance in revision review, and document
that an inferred psychological tendency does not establish a numerical score.

**Deliverable:** a reviewed pilot draft showing behavioral support, provisional
subjective tendencies, and unknown traits distinctly, without changing scores.

## Verification and acceptance

Run focused tests from `shrecknet/`, starting with the trait, personality evolution,
embodiment, and persistence modules; then run the affected package suite when
practical. Extend existing suites rather than duplicating their setup.

Required cases include malformed candidate isolation, wrong-character and
cross-scene attribution, invented excerpts, retry/checkpoint replay, duplicated
events across sources, mixed poles, manual overrides, older revisions, draft
acceptance/deletion, administrator access, and ontology isolation. Verify that
experiential input cannot affect directional scores, history, or Steadiness.

Compare baseline and candidate on identical frozen inputs and settings. Require:

1. Existing behavioral regression cases and scoring mathematics remain unchanged.
2. Generated-only reference cases never enter source-supported recurrence.
3. Repeated-event cases never gain independent support from rephrasing or refresh.
4. Every admitted experiential candidate has traceable, correctly attributed text;
   reviewers assess diagnostic significance and situational confounds separately.
5. Johnny gains useful differentiated hypotheses only where the sources justify
   them; neither all eight non-null traits nor a minimum profile density is a goal.
6. Normal stage call count stays unchanged. Record token, latency, truncation, and
   retry changes: larger responses may still increase cost or recovery calls.

Set acceptable review-error and token/latency budgets before viewing live results.
Synthetic tests establish contracts; they do not establish psychological validity.

## Rollout, compatibility, and documentation

The first delivery is offline evaluation only. Production rollout is opt-in for a
selected draft after review; existing agents, scores, and accepted portraits are
not automatically rewritten. Existing-agent refreshes retain processed scenes, so
they do not retroactively evaluate Johnny's old scenes: use frozen offline replay
for that assessment rather than promising a prompt-only backfill.

Persisting the channel should need no new graph node type or destructive migration;
verify missing-property reads and checkpoint compatibility before deployment.
Deploy API/workers together. Preserve pilot exports; rollback stops future
experiential processing, while restoring an accepted portrait requires the saved
version. Numerical experiential weighting is a separate future decision, requiring
its own evaluation and explicit evidence-policy change.

During implementation, update [Dispositional Traits](Dispositional%20Traits.md),
[CharacterAgent](CharacterAgent.md), and the
[endpoint contract](CharacterAgent%20-%20Endpoints.md) as their behavior changes.
Update SDK documentation/examples when public responses change. These canonical
pages continue to describe the current implementation until that delivery.
