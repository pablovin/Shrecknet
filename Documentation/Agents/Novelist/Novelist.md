# Novelist Agent

Novelist V5 converts a complete RPG session, notes, recap, Adventure output,
event log, or narrative into a literary chapter. The implementation belongs to
`shrecknet/app/jobs/novelist/`; `shrecknet/app/tasks/novelist.py` coordinates
persistence and background-job tracking.

## Narrative contract

A beat is a meaningful dramatic development: establishing a situation, introducing
conflict, raising stakes, making a decision, revealing information, resolving a
conflict, or creating consequences. Paragraphs, conversations, combat rounds, and
gameplay actions are not automatic beats. Related actions form one development;
smaller relevant actions remain in original passages for the writer.

Planning targets approximately 8–10 beats for a typical three-hour RPG session.
This is editorial guidance, not a minimum, maximum, or acceptance quota. Short
sources may need fewer and complex stories more. Plans must cover the beginning,
middle, and final fictional development without inventing outcomes or resolving
uncertainty. Table chatter, administration, and rules explanations are excluded
unless their fictional consequences matter.

## Source index and references

`segment_source` creates ordered `passage-000001` identifiers from paragraphs,
splitting long paragraphs at text boundaries into fragments of at most 1,800
characters. Transport windows do not redefine these reference units. Concatenating
passage text reproduces the canonical input exactly, including whitespace.
Artifacts record `source_index_version: "paragraph-v1"` and `source_segments`:

```json
{"id":"passage-000001","text":"Original passage\n\n","start":0,"end":18,"pages":[1]}
```

`start`/`end` are inclusive/exclusive character offsets in canonical extracted
text. They are not PDF byte offsets or guaranteed visual paragraphs. `pages` lists
original PDF page numbers and is empty for pasted text and non-PDF uploads. PDF
extraction retains original page numbers even across empty pages. An exception
extracting a page rejects the upload rather than silently losing that page.
Image-only pages still require upstream OCR; this adapter does not perform OCR.

The backend persists validated PDF page spans in run `settings.source_pages` and
passes them to the job from the stored run. Public request models need no new
fields. Historical uploads cannot acquire missing page provenance retroactively.

Each new story plan has this strict JSON shape:

```json
{
  "title": "The Road Through Winter",
  "cast": [{"player":"Pietro","character":"Tamura"}],
  "beats": [{
    "beat_id":"beat-001",
    "summary":"The search for shelter becomes an investigation when the villagers reveal an abduction.",
    "importance":"major",
    "source_ranges":[{"start_id":"passage-000001","end_id":"passage-000006"}]
  }],
  "continuity_notes":null
}
```

All shown keys are required; additional properties are forbidden. `title` is null
or at most 120 characters. `cast` has 0–10 explicit mappings with 1–60 character
names. `beats` is non-empty with no count maximum. Each summary has 1–600 characters;
unique `beat_id` values have 1–32 characters. `importance` is `major`, `supporting`,
or `transition`. `continuity_notes` is null or at most 2,000 characters.

Each beat has 1–8 inclusive `source_ranges`, with exact 1–32 character passage IDs
as endpoints. Ranges may be noncontiguous. Unknown IDs, reversed ranges, and
identical duplicate ranges are rejected. Overlapping selections are deduplicated
in source order. Different beats may share passages when context requires it.
Backend resolution uses index positions, not lexical ID comparisons.

## Planning and context budgets

The normal path sends all indexed source passages in one global planning call.
The source remains authoritative; subsequent writers receive relevant originals.
For oversized input, bounded transport windows produce chronological factual
inventories (`observations` with `summary` and `source_ranges`, plus explicit
`cast`). They are not local chapter plans and have no local beat quota. One global
synthesis combines all inventories and the ordered passage IDs into the chapter
plan, without resending the entire oversized original. If even synthesis cannot
fit, the run fails with an actionable context-budget error; inventories are never
silently discarded or truncated.

Only analysis calls receive strict response schemas. The backend checks complete
JSON, JSON Schema, Pydantic invariants, and range references inside one bounded
repair loop. Invalid or token-truncated output gets one complete regeneration.
Both initial and repair analysis calls allow 6,000 output tokens. There is no
schema-less provider fallback or acceptance of partial JSON.

`model_novelist_analysis` and `model_novelist_writer` remain independent targets.
Context settings, exposed through the configuration API and Novelist configuration
group, are:

| Setting | Default | Minimum |
| --- | --- | --- |
| `novelist_analysis_context_tokens` | 64,000 | 24,000 |
| `novelist_writer_context_tokens` | 64,000 | 24,000 |

Set these capacities no higher than the selected provider/model supports. The
worker does not infer context capacity from model names. Preflight conservatively
counts UTF-8 bytes of prompt and serialized schema plus 256 framing units, reserves
output tokens, and uses at most 80% of configured capacity. This is a deliberately
conservative estimate, not an exact provider tokenizer. It may select the inventory
path earlier than necessary. Every initial and repair request is checked. Oversized
instructions, continuity, a single passage, repair, or synthesis can fail clearly.
Inventory windows reserve additional headroom by packing against 80% of the
analysis context setting. Writers reserve bounded recovery space when subdividing.
Configuration uses the runtime config database; update through the configuration
API and restart workers as needed to load code changes.

## Writing and coverage

One writing unit normally corresponds to one dramatic beat. Oversized selections
split into ordered units under the same `beat_id`; subdivision never creates new
narrative beats. `block_count` counts actual writing units. Unsplittable prompt
or passage overhead fails before sending an oversized request.

Each writer receives the complete plan, its assignment, relevant verbatim
passages, lower-authority continuity, instructions, and the preceding accepted
prose tail (up to 1,800 characters). Neighboring summaries guide transitions.
Writers develop smaller relevant actions in their passages while avoiding repeated
material or consuming future beats. Importance targets are 700 words for `major`,
450 for `supporting`, and 200 for `transition`; subdivisions divide that target
with a floor of 150. Targets guide pacing, not exact word counts.

Writer calls return plain prose with a 5,000-token output budget. Local checks
reject empty/truncated output, output shorter than `max(40, 35% of target_words)`,
list-shaped output, Markdown headings/fences, and HTML. One complete-unit retry is
allowed. Failure never publishes a partial chapter. Backend escaping wraps accepted
paragraphs in `<p>` and adds the escaped `<h1>` title.

Artifacts include `coverage.beat_passages`, `unreferenced_passages`, and
`shared_passages`. These expose selection gaps/reuse without claiming semantic
completeness: table chatter can legitimately be unreferenced. Planning includes a
beginning-to-ending review; no mandatory independent fidelity model runs.
`semantic_coverage` is `not_independently_verified`, `fidelity_status` is `not_run`,
and `correction_count` is `0`. Real model quality should be evaluated against
annotated sessions; deterministic tests do not prove dramatic significance.

## Local debug artifacts

Every new execution writes incremental JSON under
`<SHRECKNET_DATA_DIR>/local_tests/novelist/<run_id>_<UTC timestamp>/`.
The data root defaults to `/data`, with a fallback to the package's
`shrecknet/databases/` directory. Compose exposes these on the host at
`shrecknet/databases/local_tests/novelist/`. Worker logs include the selected path.
Run IDs are sanitized and executions get isolated directories.

Numbered snapshots retain full runtime inputs, source indexing and planning-path
choices, stage artifacts, exact prompts and schemas, context budgets, unmodified
client responses, validation results, retry requests/responses, final HTML, and
terminal exceptions with traceback. Requests are saved before dispatch and
responses before validation, so malformed and truncated outputs survive failure.
Responses are what `ShreckLLMClient` returns, not raw provider HTTP bodies.
`manifest.json` indexes snapshots with `running`, `completed`, or `failed`, run ID,
and the last stage. A killed worker may leave `running` with previously written
snapshots. Filesystem failures warn without changing generation outcomes.

Files contain source material and generated text and live outside public media.
There is no new HTTP download route. Remove them manually; deleting a database run
does not remove its debug folder. Existing failures cannot be reconstructed;
submit a new run after rebuilding workers to capture them.

## Persistence, rollout, and compatibility

New artifacts use `pipeline_version: "v5"`. They include the source index,
`story_plan`, deterministic `coverage`, `planning`, `continuity`, writing
`block_plan`, accepted prose `blocks`, local quality results, timings, and LLM-call
metadata. `draft_text` remains safe chapter HTML. Historical V1–V4 records are
not rewritten or parsed as V5 range plans.

`previous_novelist_run_id` accepts completed V3, V4, and V5 records. V3 contributes
its evidence ledger; V4/V5 contribute their story plan as lower-authority context.
Previous-run reference IDs are never resolved against current passages.
`previous_session_id` remains a graph-backed continuity lookup.

No database table migration or backfill is required. Rebuild workers and drain
queued jobs for the code cutover: the task name stays `novelist.generate_draft`.
Queued payloads use the code installed on the worker. New configuration keys gain
defaults without replacing stored model choices. Source indexing/debug capture
increases local storage. Rollback leaves V5 JSON readable; older workers can still
read runs but do not reuse V5 continuity. Failed runs require a fresh submission.

See [the endpoint contract](Endpoints/Novelist%20-%20Endpoints.md),
[the ordered generation flow](Generate_Draft/Generate_Draft.md), and
[the implementation plan and evaluation status](Narrative%20Planning%20and%20Source%20References%20Plan.md).
