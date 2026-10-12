# Novelist Agent Endpoints

All endpoints require authentication and retain the `/jobs/novelist` prefix.

## Remembered preferences

The frontend retrieves the selected agent's nullable
`novelist_last_language` and `novelist_last_instructions` through `GET /agents/`
or `GET /agents/{agent_id}`. Omitted or `null` values reuse the saved preference.
An explicit empty string clears it. Preferences are updated when a run is accepted.

## Run lifecycle

- `POST /jobs/novelist/{agent_id}/runs` accepts `unstructured_text`, optional
  `language`, `instructions`, `previous_session_id`, `previous_novelist_run_id`,
  `source_type`, and `source_label`.
- `POST /jobs/novelist/{agent_id}/runs/upload` accepts `.txt`, `.md`, `.json`, and
  `.pdf`, plus the same optional continuity and source fields.
- `GET /jobs/novelist/runs/{run_id}` and
  `GET /jobs/novelist/{agent_id}/runs` return `NovelistRunRead`.
- `DELETE /jobs/novelist/{agent_id}/runs/{run_id}` deletes a run.

`previous_novelist_run_id` may identify a completed V3, V4, or V5 run.
Unknown, incomplete, or unsupported historical runs contribute no previous-run
context and do not override current source material.

Each extraction, synthesis, inventory, and Narrative Director call has one bounded retry, with a fixed 6,000-token output budget
on both attempts and schema-enforced content limits, as described in the
[generation flow](../Generate_Draft/Generate_Draft.md). If it still fails, the run
has `status: "failed"` and an `error_message`, without a partial chapter. Submit
a new run after resolving the cause; failed runs do not resume automatically.
New worker executions retain prompts, generated responses, and validation failures
in the [local debug area](../Novelist.md#local-debug-artifacts). This is a
filesystem diagnostic facility; endpoint payloads and authorization are unchanged.

`draft_text` remains safe, display-ready chapter HTML. V5 returns
`pipeline_version: "v5"`, `block_count`, `fidelity_status: "not_run"`, and
`correction_count: 0`. The fidelity fields remain present only for response
compatibility; V5 does not execute an LLM fidelity stage. Old V2 scene and critic
diagnostics remain nullable compatibility fields.

Clients should render server-provided progress text and must not branch core UX on
fixed stage names or artifact internals.

Refined V5 `block_count` counts arc writing units (including bounded subdivisions).
`artifacts.story_plan.beats[].source_ranges` references inclusive passage ranges;
historical V4 `source_ids` retain their original meaning. PDF uploads retain
original page spans in run settings/artifacts. A page extraction exception returns
HTTP 400 rather than silently skipping that page. Text, Markdown, JSON, and pasted
input need no metadata changes. Context preflight errors fail the asynchronous
run with an actionable `error_message`; adjust the documented role context
settings only within the selected model's supported capacity.

Refined runs retain `pipeline_version: "v5"` and add artifact revision
`narrative-director-v4`, `candidate_extraction`, `directed_story_plan`, and per-call
usage/efficiency diagnostics. Existing `story_plan` fields remain compatible.
Director progress remains `interpretation`; an `analysis_step` detail identifies
it without adding a persisted stage enum. Director failure follows the same
asynchronous failed-run contract. No new creation fields or model configuration
are required. Historical V5 runs retain their beat-unit meaning and artifacts.

New runs validate a whole-source narrative plan of at most 12 beats, including
oversized-document synthesis. Over-limit plans get one complete regeneration;
exhausted repair fails the background job before writing. Source paragraph/page
count does not determine beat count. Historical artifacts retain their original
shape and counts. See the [canonical contract](../Novelist.md).

A completed run can contain editorial identity warnings. In `artifacts.blocks[]`,
`warnings` lists persistent player/GM name mentions after one corrective retry.
`artifacts.quality_gate.all_blocks_warning_free` and `warning_count` summarize them;
`all_blocks_accepted` still means every block passed the hard checks. These additive
artifact fields do not change endpoint payloads, statuses, or auth. Historical
artifacts may lack them. See the [warning policy](../Novelist.md#writing-and-coverage).

Identity-only writer recovery edits the generated unit using confirmed mappings;
other failures regenerate the unit. Both share one retry, and every result is
revalidated. This does not add endpoint fields, model settings, or job stages.
