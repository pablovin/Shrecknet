# Novelist v3

Use `sdk.novelist.start_run(agent_id, NovelistRunCreate(...))` for pasted
transcripts, recaps, notes, Adventure output, event-log JSON, or existing prose.
Set `source_type` only as a hint; Novelist interprets the source semantically.

`draft_text` is final chapter HTML. A completed V3 run also exposes
`pipeline_version`, `block_count`, `fidelity_status`, and `correction_count`.
To reuse factual continuity, pass the completed prior run ID as
`previous_novelist_run_id`. `previous_session_id` remains graph-backed,
non-authoritative continuity text.

`start_run_from_upload` accepts PDF, text, Markdown, and JSON paths.

To prefill a Novelist form, use `sdk.agents.get(agent_id)` or
`sdk.agents.list(job="novelist")`. The returned `AgentRead` contains nullable
`novelist_last_language` and `novelist_last_instructions`. Omitting either field
(or sending `None`) on the next run reuses its saved value; send an empty string
to clear it.
