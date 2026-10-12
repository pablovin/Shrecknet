# Novelist V5

Use `sdk.novelist.start_run(agent_id, NovelistRunCreate(...))` for pasted
transcripts, recaps, notes, Adventure output, event-log JSON, or existing prose.
Set `source_type` only as a hint; Novelist creates a compact story plan and writes
sequential prose sections from it and the relevant original source.

`draft_text` is final, backend-escaped chapter HTML. A completed V5 run exposes
`pipeline_version`, `block_count`, `fidelity_status`, and `correction_count`.
Because V5 has no mandatory fidelity-model pass, the last two values are
`"not_run"` and `0`.

Pass a completed V3, V4, or V5 ID as `previous_novelist_run_id` for lower-authority
continuity. `previous_session_id` remains graph-backed continuity text.
`start_run_from_upload` accepts PDF, text, Markdown, and JSON paths.

To prefill a form, use `sdk.agents.get(agent_id)` or
`sdk.agents.list(job="novelist")`. Omitting `language` or `instructions` (or
sending `None`) reuses the saved value; send an empty string to clear it.

Refined V5 extracts candidate evidence, directs it into chronological narrative
arcs, then writes one unit per arc. Session 9 guidance is approximately 4–6 arcs,
not a hard quota. `block_count` includes bounded context/output subdivisions.
Artifacts retain `pipeline_version: "v5"`, add revision `narrative-director-v2`,
`candidate_extraction`, `directed_story_plan`, and call usage/efficiency diagnostics.
Existing `artifacts.story_plan.beats[].source_ranges` remain inclusive
`start_id`/`end_id` references; `source_segments` maps them to text, offsets, and
available PDF pages. Historical V5 beat units and earlier artifacts retain their
original meaning. Public request models and resource calls are unchanged.

Worker context settings and debug files are described in the
[canonical Novelist contract](../../Documentation/Agents/Novelist/Novelist.md).
Creation payloads and SDK calls are unchanged. Failed runs do not resume; submit
a new run after resolving context, provider, or output-validation failures.
