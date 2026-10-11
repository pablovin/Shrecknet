# Behavioral identity synthesis plan

**Status:** runtime contract, evidence-checked Steadiness input, regression tests,
29 frozen cases, and offline/live comparison tooling implemented. Live narrative
comparison, repeated decision trials, and a reviewed production pilot remain
pending; the local shreckLLM health check returned a connection error during
implementation. No behavioral-quality improvement has been established. This
record retains the design and remaining evaluation protocol. The current
contract is documented in [CharacterAgent](CharacterAgent.md) and
[Dispositional traits](Dispositional%20Traits.md).

Implementation uses `character-embodiment-v42-behavioral-identity` and
`evidence-synthesis-v2-behavioral-context`. See the
[evaluation commands and report contracts](Dispositional%20Traits.md#behavioral-identity-evaluation).

## Implementation verification

From `shrecknet/`, with `SHRECKNET_MEDIA_ROOT=/tmp/shrecknet-identity-test-media`
to override the local configuration's unwritable `/app` media directory:

- `../.venv/bin/python -m pytest tests/test_character_*.py tests/test_embodiment_validation_config.py`:
  249 passed, including the frozen-input and mocked replay checks.
- `../.venv/bin/python -m pytest`: collection blocked by existing missing imports
  in `test_architect_scene_milestone_relates_to.py`,
  `test_ontology_world_stats_endpoint.py`, and `test_scene_centric_chunking.py`
  (`ArchitectOrchestratorV2`, `OntologyInstance`, and `_normalize_scene_ranges`).

From `python_sdk/`,
`../.venv/bin/python -m pytest tests/test_character_agent_sdk.py tests/test_character_embodiment_sdk.py tests/test_character_profile_assignments_sdk.py`:
7 passed. Live shreckLLM health check failed with `ConnectError`; no live synthesis,
decision trial, or production portrait replacement was performed.

## Objective and assumptions

Make the persisted identity useful for predicting a character's tendencies in
particular circumstances. Preserve individual motives, contradictions, and both
trait poles without turning scores into guaranteed actions or making every
character reasonable, cooperative, or conscientious.

The supplied design notes reference Whole Trait Theory and
`personality_narrative_characters.pdf`. The PDF was not available for this review;
the notes are design input, not a verified scientific specification. Review the
paper before documenting scientific claims or claiming construct validity.
Shrecknet's trait registry and scoring rules remain authoritative.

## Verified baseline before implementation

- `IDENTITY_DESCRIPTION_PROMPT` in
  `shrecknet/app/jobs/character_agent/embody_agent_prompts.py` already distinguishes
  score direction from confidence, mixed from unknown estimates, manual from
  inferred direction, and enduring dispositions from temporary emotions.
- `synthesis_traits()` in
  `shrecknet/app/services/character_trait_analysis.py` supplies eight directional
  estimates, continuous inference, unweighted pole counts, and at most four
  representative behavioral examples per trait. Examples retain both poles when
  available and are restricted to contributing evidence. Steadiness is excluded.
- Synthesis also receives canonical identity, current goals/aspects, at most six
  recent validated perspectives, and bounded previous text explicitly marked
  `continuity_only_not_evidence`.
- `EmbodyAgent.generate_identity_description()` owns the model request. Both draft
  generation in `app/tasks/character_embodiment.py` and append processing in
  `app/services/character_embodiment_service.py` use it. Initialization generates
  only when identity is absent; a final synthesis refreshes the persisted portrait.
- Decision making in `app/jobs/character_agent/decision_making.py` receives this
  portrait as its sole personality input, alongside caller context and retrieved
  subjective memories. Numerical traits, Steadiness, goals, and aspects are not
  sent separately. Therefore synthesis can affect both later scene interpretation
  and later decisions; evaluation must distinguish these effects.

The main gap is an explicit requirement to connect supported situations,
interpretations, motives, competing tendencies, and likely responses. It is not
a missing evidence-extraction stage.

## Proposed output responsibilities

Keep the existing public object and field types:

| Field | Responsibility after the change |
| --- | --- |
| `identity_summary` | Background, role, defining experiences, and recognizable identity. Preserve the existing purpose. |
| `psychological_summary` | How established beliefs, goals, fears, and values shape interpretations and resolve or sustain conflicts. Include supported competing motives and conditional responses. |
| `personality_traits` | Brief, character-specific tendencies for supported directional traits, with relevant activation conditions and exceptions only where evidence supports them. |

Keep prose concise and avoid repeating the same explanation in all three fields.
Do not add a mandatory mechanism or exception for every trait: missing evidence
must result in a narrower statement, not invented psychology. Unknown traits
remain omitted; an empty `personality_traits` list remains valid. Steadiness does
not become a ninth entry.

## Work package 1: Refine the synthesis contract — implemented

Update `IDENTITY_DESCRIPTION_PROMPT` and its maintainer header to require:

1. Treat scores as relative tendencies on the registry's bipolar dimensions,
   never action probabilities, moral rankings, or deterministic instructions.
2. Connect a disposition to supported circumstances and its observable
   manifestation. Explain motives or expectations only when supplied evidence or
   accepted psychological context supports them; an action alone does not prove
   its internal cause.
3. Describe interactions between dispositions when supported. Do not infer a
   conflict merely because two scores are high. Specific goals, relationships,
   knowledge, capability, and opportunity may outweigh a general tendency.
4. Describe low-pole tendencies in their own terms using the registry. Do not
   soften exploitative, retaliatory, self-serving, or careless behavior into
   universally positive traits, or caricature every low score as dysfunction.
5. Preserve the current evidence hierarchy: subjective perspectives explain
   interpretations without proving objective actions; prior summaries preserve
   continuity without supplying new evidence; manual scores control direction
   without making conflicting observations disappear.
6. Separate temporary state, enduring motivation, and dispositional tendency.
   Use conditional language without introducing unsupported exceptions.

For example, if evidence establishes both investigation and protective
preparation, a summary may say that dangerous mysteries attract the character
but usually lead them to gather information or arrange protection first. A high
Caution score and high Curiosity score alone do not establish anxiety, a need for
reassurance, or circumstances in which curiosity wins.

Retain the complete input and output contracts in the prompt. No new model call,
runtime PDF input, output field, persistence object, or trait formula is needed.

## Work package 2: Supply separate Steadiness context — implemented

Extend the existing synthesis input assembly with a separate `steadiness` object.
Use the existing `TraitProfile.steadiness` estimate and registry definition;
keep `synthesis_traits()` restricted to directional traits.

The model-facing contract should explicitly describe `value` (integer 1–9 or
null), `status` (`unknown`, `provisional`, `supported`, or `manual`), and
`observation_count` (contributing perspective count, not confidence). Include
the registry's meaning and both poles. Any added availability field must be
validated against the contributing evidence; it must not imply evidence is
available merely because a point is present. Define and test missing-evidence and
manual-override handling before using those records to support narrative claims.

High values mean narrower observed variation and low values broader variation
in comparable situations. This is an aggregate engineering estimate, not a
per-trait variance measurement. It does not establish a trigger, psychological
cause, emotional stability, reliability, or model temperature. Unknown values
must produce no consistency claim; provisional values require tentative wording.
Do not project global consistency onto every individual disposition.

Keep assembly and evidence handling in the existing analysis/service owner and
request construction in the job. Update `SYNTHESIS_VERSION` and `PROMPT_VERSION`
together with their documented contracts and checkpoint expectations. Preserve
the existing validation and replacement budget.

## Work package 3: Verify contracts and narrative quality — tooling implemented; live review pending

Extend existing tests rather than creating a parallel synthesis implementation:

- `test_character_personality_evolution.py`: separate Steadiness input, unknown
  and provisional estimates, manual overrides, missing evidence, bounded context,
  contributing-evidence filtering, and unchanged directional payloads.
- `test_character_embodiment.py` and `test_character_embodiment_jobs.py`: both
  initialization and final refresh, draft and append paths, acceptance/persistence,
  existing retry behavior, and unchanged normal call counts.
- `test_character_traits.py`: retain the current scoring and comparable-context
  Steadiness regression cases without changing their expected mathematics.
- `test_character_agent_decision_making.py`: preserve the sole personality input,
  identity-unavailable failure, generic mode, and character-memory isolation.

Add frozen synthesis cases for both poles, unknown and sparse evidence, a mixed
score, supported and unsupported trait interactions, state versus disposition,
manual/inferred disagreement, and unknown/high/low Steadiness. Include equal-score
characters with different supported motives and cases with no motive evidence.

Mocked tests verify transport, schemas, persistence, and failures. Prompt-text
assertions alone cannot establish better psychological synthesis. Compare captured
baseline and candidate outputs with a review rubric covering evidence fidelity,
pole fidelity, conditional specificity, unsupported causes/exceptions, and
repetition. Keep fixture facts and reviewer expectations separate from production
character evidence. Record prompt/input hashes, model/settings, failures, token
usage, and reviewed outputs.

Run focused tests from `shrecknet/` first, then the affected CharacterAgent suite
when practical. Run SDK compatibility tests if shared public projections change.

## Work package 4: Measure the effect on decisions — tooling implemented; live trials pending

First isolate summary quality from extraction and retrieval:

1. Freeze canonical identity, trait profile, evidence, goals/aspects, and
   perspectives. Generate baseline and candidate portraits from identical inputs.
2. Give the same deliberation model identical situations, action options,
   knowledge, capabilities, opportunities, instructions, and memory context.
   Change only the portrait. Use controlled offline inputs so owner-specific
   retrieval cannot confound this comparison.
3. Compare repeated trials with fixed model/settings and recorded seeds where
   supported. Include opposing poles, equal scores with different supported
   motives, competing goals, non-diagnostic situations, and neutral-mode controls.
4. Review action distributions and evidence-grounded character fit, not just
   explanations. Blind reviewers to the prompt version where practical. Set the
   rubric, repetition budget, and acceptance criteria before inspecting results.

Accept the candidate only if it improves supported conditional characterization
and relevant decision sensitivity without increasing unsupported psychology,
pole distortion, or rigid stereotyped choices. Do not require every character to
choose differently or force equal pole frequencies. Report uncertainty and
inconclusive results; a single generation does not establish an improvement.

Then run a small end-to-end draft and query pilot using normal subjective memory
retrieval. This second evaluation includes pipeline feedback and tests practical
behavior, but does not isolate personality as cleanly. Do not supply omniscient
knowledge to normal gameplay or change production retrieval for the experiment.

The existing `character_trait_evaluation.py` evaluates extraction. Reuse its
reporting conventions where appropriate, but keep extraction scores distinct
from synthesis review and decision-distribution results.

## Documentation, rollout, and rollback

At implementation time, update the canonical identity-synthesis section in
[CharacterAgent](CharacterAgent.md), the interpretation and rollout rules in
[Dispositional traits](Dispositional%20Traits.md), and qualitative-summary
semantics in [endpoint documentation](CharacterAgent%20-%20Endpoints.md) and
`python_sdk/docs/character_agents.md`. Update the prompt module's ordered flow.
Public schema changes are outside this proposal.

Deploy compatible API and workers together. Preserve existing portraits and
ledgers on deployment; do not silently resynthesize all characters or re-extract
historical scenes. Use reviewed embodiment drafts for the pilot and verify the
existing append path separately. Record/export pilot portraits before replacement.
Version changes must prevent reuse of incompatible checkpoints. No database
migration or memory re-embedding is expected.

Rolling back prompt code affects future generation; it does not restore already
accepted portraits. Restore saved compatible portraits through an authorized
maintenance path when necessary. Budget for bounded input/token growth, including
existing validation retries, while retaining the normal production call count.

Deliver in order: synthesis contract and Steadiness context with focused tests;
frozen narrative comparison; controlled decision evaluation; reviewed pilot.
Mark this plan implemented only when code, tests, canonical documentation, and
reported evaluation evidence agree.
