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

Extraction produces candidate evidence, not the final literary outline. It records
fiction, mixed fiction/gameplay, and gameplay-only developments, confirmed facts,
unresolved possibilities, and explicit identity evidence. A possible treatment is
not a completed healing; falling damage is not evidence of death. Explicit user
mappings take precedence during extraction. Ambiguous players retain a null
character; the GM is identified and excluded from fiction.

One Narrative Director call groups candidates into chronological literary arcs.
It receives only candidate JSON, instructions, and language, using the existing
`model_novelist_analysis`. It never receives original passages, the PDF, previous
prose, or historical continuity. About 4–6 arcs is guidance for Battle of Badgrain
Session 9, not a count quota. Normal writing uses one assignment per arc.

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

`artifacts.story_plan` retains this legacy V5 projection for compatibility:

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

## Candidate extraction and directed plan

`artifacts.candidate_extraction` retains the legacy plan fields plus `identities`.
Each beat additionally requires `material` (`fiction`, `mixed`, or `gameplay`),
`confirmed_facts`, and `unresolved_facts`. Each fact array holds 0–12 statements of
1–300 characters. Identities have unique `player` names and this shape:

```json
{"player":"Enrico","character":null,"status":"ambiguous"}
```

`status` is `confirmed`, `ambiguous`, or `gm`. Only confirmed identities have a
nonempty `character`; the others require null. Names are at most 60 characters,
with at most ten identity entries. Candidate cast mappings require confirmed
identity evidence. Simple explicit instruction lines (`Pietro is Tamura`,
`Laura plays Lynelle`, `Pablo is the DM`, `Enrico is Cwenhild or Asterius`)
are checked locally inside extraction repair; a Director cannot endorse a cast
that contradicts those instructions. First-name instructions may match extracted
full player names. Unrecognized free-form wording remains an LLM contract.
Extraction and oversized factual inventories use the same
certainty/material fields, so synthesis must preserve evidence and uncertainty.

`artifacts.directed_story_plan` has the following complete structured shape:

```json
{
  "title": "The Battle of Badgrain",
  "canonical_cast": [{"player":"Pietro","character":"Tamura"}],
  "identity_restrictions": [{"player":"Enrico","character":null,"status":"ambiguous"}],
  "arcs": [{
    "arc_id":"arc-001",
    "outline":"Arthur recognizes Tamura before the battle.",
    "importance":"major",
    "beat_ids":["beat-001"],
    "confirmed_facts":[{"beat_id":"beat-001","fact_index":0}],
    "unresolved_facts":[]
  }],
  "excluded_beat_ids":["beat-002"]
}
```

All keys are required and extra properties are forbidden. Titles retain the
120-character limit. Arc IDs are unique and 1–32 characters; outlines contain one
short sentence of at most 300 characters. Arcs and their beat IDs preserve candidate
order. Each candidate is assigned exactly once or explicitly excluded. Gameplay-only
candidates must be excluded; fiction and mixed candidates remain represented.
The canonical cast preserves exactly the confirmed identities. Restrictions copy
all ambiguous/GM identities without resolving them.

Facts are compact references: `fact_index` is a zero-based index into the named
candidate beat's corresponding fact array. Each arc must reference every confirmed
and unresolved fact of its assigned candidates exactly once. The backend resolves
the original statements for writers. The Director cannot rewrite a possibility
as a confirmed fact. These checks validate contracts, not the accuracy of extraction
or the semantic fidelity of prose. Extraction mistakes still require live evaluation.

## Planning and context budgets

The normal path sends all indexed source passages in one global planning call.
The source remains authoritative; subsequent writers receive relevant originals.
For oversized input, bounded transport windows produce chronological factual
inventories (`observations` with `summary` and `source_ranges`, plus explicit
`cast`). They are not local chapter plans and have no local beat quota. One global
synthesis combines all inventories and the ordered passage IDs into the candidate
extraction, without resending the entire oversized original. If even synthesis cannot
fit, the run fails with an actionable context-budget error; inventories are never
silently discarded or truncated.

Extraction, inventories, synthesis, and the Director receive strict response schemas. The backend checks complete
JSON, JSON Schema, Pydantic invariants, and range references inside one bounded
repair loop. Invalid or token-truncated output gets one complete regeneration.
Every initial and repair analysis call allows 6,000 output tokens. There is no
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

One writing unit normally corresponds to one Director arc. `block_count` counts
actual units, including deterministic subdivisions required by input-context or
output limits. Units retain their `arc_id`, ordered `beat_ids`, source selection,
and `part_index`/`part_count`. Source selections within an arc are deduplicated and
subdivision preserves every selected passage in order. A shared candidate summary
is context, not permission to re-render earlier source passages.

The chapter target is twice the source word count, bounded to 700–6,000 words,
independent of candidate count. This allows literary development of concise
session notes without multiplying chapter length by candidate count. Arc importance weights are 3/2/1 for
`major`/`supporting`/`transition`; largest-remainder allocation preserves the total
word target. Units target at most 1,400 words under the existing 5,000-output-token
limit. If required subdivision has no source passage boundary, fail clearly rather
than repeat evidence or discard content. Targets guide pacing, not exact word counts.

Each writer receives original instructions/language, canonical cast and restrictions,
one short outline sentence per arc, current assignment details only, selected
verbatim passages, and up to 700 characters of preceding accepted prose. It never
receives the complete candidate extraction, other arcs' detailed facts, the whole
source by default, or complete preceding blocks. A short source may naturally fit
entirely within one arc. Full historical continuity stays in artifacts; matching
prior narrative text is bounded to 1,000 characters in writer context (without
name filtering when no explicit mappings exist) and remains
lower authority than current evidence. Historical reference IDs are not resolved
against current passages.

Writers return plain continuous literary prose. Local checks reject empty/truncated
output, output shorter than `max(40, 35% of target_words)`, list-shaped output,
Markdown headings/fences, HTML, forbidden player/GM identities, and explicit RPG
administrative phrases in English, Italian, and French. Canonical name collisions
are allowed; whole-name matching avoids substring false positives. Checks also
reject repeated substantial paragraphs and exact 40-word spans from earlier
accepted sections. They do not detect every paraphrased repetition, invented event,
missing outcome, ambiguous attribution, or RPG term in every language.

One complete-unit retry is allowed. Only accepted prose advances the transition
tail. Exhausted analysis/Director/writer repair fails the run without publishing a
partial chapter. Backend escaping wraps accepted paragraphs in `<p>` and adds the
escaped `<h1>` title.

Coverage retains `beat_passages`, `unreferenced_passages`, and `shared_passages`, and
adds `arc_beats` and `excluded_beat_ids`. Selection gaps/reuse do not establish
semantic completeness. `semantic_coverage` remains `not_independently_verified`,
`fidelity_status` is `not_run`, and `correction_count` is `0`. No mandatory LLM
critic, semantic reviewer, embedding retrieval, or second full-source pass runs.

## Efficiency and evaluation

Each dispatched call records provider `usage`, reported input/output tokens when
available, prompt/schema UTF-8 bytes, conservative preflight cost, elapsed time,
pipeline repair/retry status, model metadata, and writer block ID. Client/provider
transport retries are not counted as separate pipeline repair attempts. Missing usage remains null and
is counted separately from estimates. Failed provider calls retain timing/context
diagnostics. `artifacts.efficiency` aggregates calls, writing calls, retries, writer
prompt bytes, reported tokens, and missing-usage count. Timings include extraction,
Director, writing, merging, and the full execution.

The Session 9 fixture and reproducible comparison command, measured baseline, and
live-evaluation limitations are documented in the
[planning and evaluation record](Narrative%20Planning%20and%20Source%20References%20Plan.md).

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

New artifacts use `pipeline_version: "v5"` and
`pipeline_revision: "narrative-director-v1"`. They include the source index,
`story_plan`, deterministic `coverage`, `planning`, `continuity`, writing
`block_plan`, accepted prose `blocks`, local quality results, timings, and LLM-call
metadata. `draft_text` remains safe chapter HTML. Historical V1–V4 records are
not rewritten or parsed as V5 range plans.

`previous_novelist_run_id` accepts completed V3, V4, and V5 records. V3 contributes
its evidence ledger; V4/older V5 contribute their story plan; refined V5 contributes its directed plan
as lower-authority context.
Previous-run reference IDs are never resolved against current passages.
`previous_session_id` remains a graph-backed continuity lookup.

Existing persisted stage enums and public creation payloads are unchanged. Director
activity is reported under `interpretation` with `analysis_step: "narrative_director"`.
No new model configuration, database table migration, or backfill is required. Rebuild workers and drain
queued jobs for the code cutover: the task name stays `novelist.generate_draft`.
Queued payloads use the code installed on the worker. New configuration keys gain
defaults without replacing stored model choices. Source indexing/debug capture
increases local storage. Rollback leaves V5 JSON readable; older workers can still
read runs but do not reuse V5 continuity. Failed runs require a fresh submission.

See [the endpoint contract](Endpoints/Novelist%20-%20Endpoints.md),
[the ordered generation flow](Generate_Draft/Generate_Draft.md), and
[the implementation plan and evaluation status](Narrative%20Planning%20and%20Source%20References%20Plan.md).
