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

V5 plans group related fictional actions into dramatic developments, typically
8–10 beats for a three-hour session. This is a soft target. Writing units usually
correspond to one beat; `block_count` includes subdivisions needed for context
sizing. New `artifacts.story_plan.beats[].source_ranges` contain inclusive
`start_id`/`end_id` passage references. `artifacts.source_segments` maps these
to original text, character offsets, and original PDF pages when available.
Historical artifacts retain their version and reference shape.

Worker context settings and debug files are described in the
[canonical Novelist contract](../../Documentation/Agents/Novelist/Novelist.md).
Creation payloads and SDK calls are unchanged. Failed runs do not resume; submit
a new run after resolving context, provider, or output-validation failures.
