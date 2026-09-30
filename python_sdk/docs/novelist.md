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
