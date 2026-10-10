# Novelist Agent

Novelist V4 turns transcripts, notes, recaps, Adventure output, event logs, and
existing prose into a continuous literary chapter. Its implementation is owned by
`shrecknet/app/jobs/novelist/`; `shrecknet/app/tasks/novelist.py` is the async
entry point.

## Runtime contract

The only active execution path is:

```text
source → compact story plan → continuity → deterministic writing blocks
       → sequential plain prose → local completion checks → safe HTML rendering
```

The analysis model acts as an editor. It extracts a title, explicit cast mappings,
ordered narrative beats, coarse source references, and optional continuity notes.
It does not build an evidence ledger or classify individual claims. The writer
model acts as a novelist and writes the planned material as prose.

The two model roles remain independently configurable through
`model_novelist_analysis` and `model_novelist_writer`.

## Structured analysis contract

Analysis and long-input reconciliation use the same strict JSON Schema. Every
object sets `additionalProperties: false`, and every property is required. Nullable
fields are still required and use `null` when absent:

```json
{
  "title": "The Road Through Winter",
  "cast": [
    {"player": "Pietro", "character": "Tamura"}
  ],
  "beats": [
    {
      "beat_id": "beat-001",
      "summary": "Tamura reaches the isolated village.",
      "importance": "major",
      "source_ids": ["source-0001"]
    }
  ],
  "continuity_notes": null
}
```

`importance` is `major`, `supporting`, or `transition`. `beats` and each
`source_ids` array must be non-empty. Beat IDs must be unique, and source IDs must
identify source segments supplied to the analysis call. `cast` is an array rather
than an arbitrary-key object so provider-native strict schemas can enumerate and
require every nested field.

Only analysis-model calls receive this JSON schema, on both the initial request and
the bounded repair request. V4 does not use a schema-less provider fallback: a
provider that rejects structured output fails the run clearly. Writer-model calls
explicitly receive no response schema because they return plain prose. Analysis is
parsed as one complete JSON document, checked against the schema, and retried once
when it is malformed, incomplete, schema-invalid, or reports token-limit truncation.

For oversized input, source text is bounded into segments. Each segment produces a
compact partial plan, and one analysis reconciliation call produces the final plan.
The same schema and validation rules apply to partial and reconciled plans.

## Writing and completion checks

Adjacent beats are grouped deterministically into sections targeting 800–1,200
words. Each writer call receives the complete story plan, its assigned beats,
relevant verbatim source segments, lower-authority continuity, user instructions,
and the tail of the preceding accepted section. The final block is explicitly told
to produce a complete literary conclusion.

The writer returns plain text. Local checks reject only unusable output: empty or
token-truncated responses, unexpectedly short responses, list-shaped output,
Markdown headings or fences, and model-produced HTML. One complete-section retry
is permitted. Minor stylistic imperfections and legitimate short dramatic
paragraphs do not trigger rewriting.

The backend escapes the title and prose, wraps prose paragraphs in `<p>` elements,
and inserts the `<h1>`. There is no mandatory LLM fidelity verification,
correction, or re-verification stage.

## Persistence and compatibility

V4 artifacts use `pipeline_version: "v4"` and contain `story_plan`,
`source_segments`, `continuity`, `block_plan`, accepted plain-prose `blocks`, local
quality results, timings, and LLM-call metadata. `draft_text` remains display-ready
chapter HTML.

The request routes and payload remain backward compatible. Historical V1–V3 run
artifacts are not rewritten. `previous_novelist_run_id` accepts a completed V4 run
or a historical V3 run: V4 reuses its story plan as lower-authority continuity,
while V3 reuses its evidence ledger. `previous_session_id` remains a graph-backed,
lower-authority text lookup.

For response compatibility, `fidelity_status` is `not_run` and
`correction_count` is `0` on V4 runs. Historical stage enum values and nullable V2
diagnostic fields remain readable, but V4 does not emit fidelity or correction
stages.

See [the endpoint contract](Endpoints/Novelist%20-%20Endpoints.md) and
[the generation flow](Generate_Draft/Generate_Draft.md).
