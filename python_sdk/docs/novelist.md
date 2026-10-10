# Novelist V4

Use `sdk.novelist.start_run(agent_id, NovelistRunCreate(...))` for pasted
transcripts, recaps, notes, Adventure output, event-log JSON, or existing prose.
Set `source_type` only as a hint; Novelist creates a compact story plan and writes
sequential prose sections from it and the relevant original source.

`draft_text` is final, backend-escaped chapter HTML. A completed V4 run exposes
`pipeline_version`, `block_count`, `fidelity_status`, and `correction_count`.
Because V4 has no mandatory fidelity-model pass, the last two values are
`"not_run"` and `0`.

Pass a completed V3 or V4 ID as `previous_novelist_run_id` for lower-authority
continuity. `previous_session_id` remains graph-backed continuity text.
`start_run_from_upload` accepts PDF, text, Markdown, and JSON paths.

To prefill a form, use `sdk.agents.get(agent_id)` or
`sdk.agents.list(job="novelist")`. Omitting `language` or `instructions` (or
sending `None`) reuses the saved value; send an empty string to clear it.
