# Novelist Narrative Planning and Source References Plan

Status: implemented as V5. The canonical [runtime contract](Novelist.md) and
[ordered flow](Generate_Draft/Generate_Draft.md) describe current behavior. This
page retains the design rationale and acceptance plan. Automated regression checks
cover the implemented contracts; live model evaluation and comparison against
annotated real sessions remain pending. Context capacity is explicitly configured;
preflight uses a conservative UTF-8 byte estimate, not model-name inference.
Oversized synthesis fails clearly rather than recursively reducing inventories.
Paragraph indexing, PDF page provenance, global planning, factual-window fallback,
range repair, beat writing, diagnostics, and V3/V4/V5 continuity are implemented.
The implementation uses 600-character summaries and 6,000-token analysis budgets;
semantic quality and those defaults still need calibration on real sessions.

## Objective

Turn a complete session into a compact editorial plan organized around dramatic
change, then write it from precise original passages. Cover the beginning, middle,
and conclusion without treating every source paragraph or gameplay action as a
separate beat. The source remains authoritative throughout.

The proposed flow is:

```text
Full source → ordered passage index → global narrative understanding
            → compact dramatic plan with passage ranges
            → reference validation and coverage review
            → sequential writing of each beat from original passages
            → local prose checks → safe chapter HTML
```

The normal path uses one global planning call. “Read once” means one initial
chapter-wide interpretation of the source; writers still receive relevant original
passages, and a rejected plan may require a bounded retry. Sources exceeding the
model's input budget need an explicit fallback rather than silent truncation.

## Current constraints

- `source_interpreter.segment_source` packs paragraphs into segments up to 12,000
  characters. A `source-0001` reference can select much of a session rather than a
  specific action.
- `interpret_source` switches to partial plans when there are more than four
  segments. This is a character/chunk threshold rather than a model input budget.
- `StoryBeat` describes an ordered narrative event. Prompts encourage grouping but
  do not explicitly require a dramatic development or a typical chapter scale.
- Analysis allows unlimited beat count and summaries up to 1,000 characters, while
  generation has a fixed 3,000-token limit. Per-field validity does not guarantee
  that the complete response fits. Both truncated attempts fail correctly.
- `plan_blocks` groups adjacent beats using fixed importance estimates and targets
  800–1,200 words per block. It does not write one dramatic beat at a time.
- PDF extraction normalizes pages into plain text. Original page provenance is
  not retained as a structured runtime input.

## 1. Define beats by dramatic purpose

Use this shared editorial instruction in global planning, reconciliation, and
repair prompts:

> Read the complete source as one narrative. Identify the major story beats that
> would structure a well-written novel chapter. A story beat represents a meaningful
> dramatic development, not an individual source paragraph, conversation, combat
> round, or gameplay action. Combine related events into coherent narrative
> developments. Prioritize conflicts, turning points, character decisions,
> revelations, and consequences. Exclude out-of-fiction discussions, rules
> explanations, and administrative details unless their fictional consequences
> matter. Produce approximately 8–10 beats for a typical three-hour RPG session,
> adjusting to the actual story's complexity. Cover the entire source, including
> its conclusion. Do not invent missing outcomes.

Each summary should explain the situation or conflict and what changes because of
it. Establishing a situation, raising stakes, revealing information, resolving a
conflict, and creating consequences are valid purposes. They need not all occur in
every beat. Existing `importance` values can remain; a new taxonomy is unnecessary
for the first implementation.

Eight to ten is a soft target, not a schema limit or an automatic acceptance rule.
A brief recap can need fewer beats; a complex session can need more. Never pad a
short source, merge unrelated turning points just to hit a count, or discard the
ending to fit the budget. Preserve uncertainty and unresolved endings.

For example, arrival, questioning villagers, and discovering evidence of an
abduction may form one development: “The search for shelter becomes an abduction
investigation when the villagers reveal who disappeared.” Several combat rounds
may form one confrontation; a betrayal during that confrontation may warrant its
own beat because it changes the conflict.

## 2. Separate reference granularity from transport sizing

Create a deterministic ordered passage index before calling the model. Paragraphs
or transcript turns are reference units; larger groups used to fit model requests
are transport units. Changing an analysis context window must not change passage
identities within a run.

The backend assigns stable IDs such as `passage-000001`. Each passage stores its
verbatim text and start/end character offsets into the canonical extracted text.
Split oversized paragraphs at bounded text boundaries and preserve all content.
Record the indexing version so debug artifacts can reproduce the mapping.
Paragraph identity and offsets refer to extracted text, not PDF byte positions or
guaranteed visual paragraphs.

Replace new-plan coarse `source_ids` with bounded passage ranges, for example:

```json
{
  "beat_id": "beat-003",
  "summary": "The villagers' account turns the travelers' search for shelter into an abduction investigation.",
  "importance": "major",
  "source_ranges": [
    {"start_id": "passage-000018", "end_id": "passage-000031"},
    {"start_id": "passage-000044", "end_id": "passage-000046"}
  ]
}
```

Range endpoints are inclusive. Allow noncontiguous ranges because one development
can draw evidence from separated passages. A range avoids enumerating dozens of
IDs in the compact plan. Resolve ranges by the supplied passage index, never by
model-generated text or lexical ID comparison. Validate known endpoints, forward
order, duplicate ranges, and non-empty selection. Deduplicate selected passages
while retaining source order. Shared passages across beats are permitted when
context requires them; excessive reuse is a diagnostic signal.

For PDF uploads, retain page numbers during extraction and map passages to their
original page or page span. Keep paragraph IDs as the retrieval authority and page
numbers as human-readable provenance. This needs an internal, validated source
metadata envelope carried from upload extraction through the task to artifacts;
it must not depend on re-reading a temporary upload later. Pasted text has null
page provenance. Keep existing text upload and SDK calls working without metadata.
Delegate reusable extraction/indexing behavior to the established service/job
owners instead of adding business rules to the HTTP router.

Deliver paragraph indexing first if PDF page provenance would delay the core
change; explicitly label page provenance unavailable until it is preserved at
ingestion. Never infer page numbers from the flattened string.

## 3. Plan globally with explicit input and output budgets

Send the complete indexed session to one analysis call when it fits the selected
model's usable input context. Include prompt/schema overhead, output reservation,
and a safety margin in the budget. Use a verified model context limit or an
explicit configured limit; do not infer capacity from the model name. Record the
estimate and selected path in diagnostics.

The planner returns the existing title/cast/continuity fields and a concise list
of dramatic beats with passage ranges. Short editorial summaries and range
references should reduce output size. Calibrate summary guidance and output
budgets against representative sessions before fixing new defaults. Approximately
8–10 beats alone cannot guarantee a response fits within 3,000 tokens.

Keep complete JSON parsing, strict provider schemas, Pydantic validation, and one
bounded repair. Run reference and uniqueness checks inside the same repair boundary
so invalid IDs can be repaired instead of failing after JSON validation succeeds.
A truncation retry should regenerate a shorter complete plan; any larger retry
budget must be explicitly bounded and fit the model context. Never accept sliced
JSON, silently remove beats, or keep retrying indefinitely.

For oversized sources, gather compact chronological factual inventories from
transport windows, preserving passage IDs, discoveries, decisions, consequences,
and unresolved threads. These are not chapter plans and receive no 8–10-beat quota.
One global synthesis then creates the chapter plan from those inventories. Do not
concatenate independently structured local beat lists or resend the entire
oversized source during reconciliation. Budget every fallback request, including
synthesis; use bounded inventory reduction if needed, or fail with an actionable
input-size error. Preserve provenance and report that global understanding used
summaries rather than the full source in one call.

## 4. Check coverage without equating it with paragraph count

Deterministic checks establish valid references and ordered, non-empty writing
units. They cannot prove narrative significance, completeness, or absence of
invented outcomes.

The planner should perform a beginning-to-end coverage review before returning
its plan, including the last fictional development even when it is unresolved.
Reference coverage diagnostics should expose gaps and repeated passages without
requiring every paragraph to be selected: rules discussion and administration can
legitimately remain unreferenced.

Use annotated evaluation sessions to assess missed conflicts, decisions,
revelations, consequences, and conclusions. Start with this integrated planning
review and offline evaluation rather than adding a mandatory second LLM verifier.
Add a separate coverage call only if evaluations show a concrete need.

## 5. Write sequentially from each dramatic beat

Use one writing unit per dramatic beat initially. Each call receives the overall
plan, its current beat, resolved original passages, continuity, instructions, and
the previous accepted prose tail. It should develop smaller fictional actions
inside those passages without turning each action into a new structural beat.
Give the writer neighboring beat summaries so it avoids consuming later
material, repeating earlier events, or adding a conclusion before the final unit.

Replace the unconditional 800-word minimum target with beat-appropriate targets
and proportional completion checks. Establishing a transition should not be
padded to the same length as a major confrontation. Validate any target changes
against writer output limits and representative chapter lengths. If one beat's
selected material exceeds the writer input budget, split it into ordered writing
units beneath the same beat; transport subdivisions do not create new beats.

Retain plain prose output, local structural/truncation checks, one bounded section
retry, sequential transitions, escaped HTML rendering, and no partial public
chapter on failure. `block_count` continues to count actual writing units, which
may now correspond to individual beats or subdivisions rather than grouped beats.

## 6. Ownership, artifacts, and compatibility

- `story_plan.py`: new versioned passage/range models, strict schema, and reference
  validators. Keep historical coarse-reference plans readable.
- `source_interpreter.py`: indexing and budget-aware global/fallback interpretation.
- `prompts.py`: shared beat definition and self-contained input/output contracts,
  stage headers, retries, and execution order synchronized with schemas.
- `block_planner.py`: map dramatic beats to bounded writing units without changing
  their narrative identity.
- `orchestrator.py`: resolve original passages, enforce repair boundaries, run
  writing units, and preserve diagnostic capture.
- Upload extraction/service and `tasks/novelist.py`: carry optional page provenance
  through ingestion and execution; retain existing request compatibility.

Version the new artifact contract, for example `pipeline_version: "v5"`, rather
than changing the meaning of stored V4 `source_ids`. Persist the passage index,
plan, resolved beat-to-passage mapping, coverage diagnostics, writing units, and
context/output budget decisions. Continue saving exact prompts, raw client
responses, validation errors, retries, and terminal failures in the local debug
area before validation can discard them.

`previous_run_context` currently recognizes V3 and V4 only. Extend it to reuse V5
plans as lower-authority narrative context; previous-run passage IDs must never
resolve against the current source. Preserve historical V1–V4 reads. Keep public
run creation routes and `draft_text` HTML stable. Update API/SDK documentation and
examples for the new artifact version and writing-unit meaning; update SDK models
if any typed public fields change.

No database table migration is expected if the passage index remains in run JSON
artifacts. Check storage size with full PDFs and avoid unnecessary duplicate source
copies. Rebuild workers for rollout and submit fresh runs; old failed attempts
cannot gain missing provenance retroactively. Drain jobs during version cutover.
Keep historical artifact readers usable during rollback; do not rewrite old runs.

## Implementation sequence and acceptance

1. Add representative fixtures and baseline evaluations: short notes, a typical
   session, dense combat, player chatter, long input, and an unresolved ending.
2. Add versioned passage indexing, range resolution, and optional PDF provenance.
3. Update the global planning contract and bounded long-input fallback together
   with schemas and deterministic repair validation.
4. Change writing units and source selection, including input-size checks and
   beat-appropriate length behavior.
5. Extend debug artifacts, historical continuity support, documentation, and SDK
   examples; evaluate real model runs before enabling the new path broadly.

Focused tests must cover text preservation, deterministic IDs/offsets, oversized
paragraph splitting, page mapping, noncontiguous/shared ranges, unknown/reversed
ranges, schema/Pydantic agreement, duplicate beat IDs, full-prompt budgeting,
fallback synthesis bounds, malformed/truncated analysis, exhausted repair, writer
retry/failure, previous-run isolation, safe HTML, and diagnostic retention.

Semantic acceptance uses annotated sources and recorded model output, not merely
prompt-string assertions. A typical fixture should yield approximately 8–10
meaningful developments while retaining all annotated major turns and its ending.
A short fixture may yield fewer, and a complex fixture may yield more. Dense combat
must not become one beat per round. Table chatter must not become fictional
content. Writers must receive the exact original passages selected for their unit,
including small relevant actions, without routinely receiving the whole session.
Measure beat count, selected-source size/reuse, input/output tokens, truncation,
retries, latency, chapter length, and human-assessed fidelity against the V4 baseline.

## Related documentation

- [Current Novelist contract](Novelist.md)
- [Current generation flow](Generate_Draft/Generate_Draft.md)
- [Current endpoint contract](Endpoints/Novelist%20-%20Endpoints.md)
