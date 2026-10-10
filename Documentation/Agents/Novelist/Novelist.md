# Novelist Agent

Novelist v3 turns narrative-adjacent source material into a chapter with a
creative surface and a factual core. Its implementation is owned by
`shrecknet/app/jobs/novelist/` and its async entry point is
`shrecknet/app/tasks/novelist.py`.

## Runtime contract

The only runtime path is:

```text
source → evidence ledger → continuity → deterministic writing blocks
       → quality gate → merge → fidelity verification → targeted correction
```

The analysis model interprets source material and verifies the final chapter.
The writer model writes or corrects bounded prose blocks. Configure only
`model_novelist_analysis` and `model_novelist_writer`.

Analysis responses are locally parsed and checked against their requested JSON
Schema. If a provider accepts native structured output but returns malformed
content, Novelist makes one source-preserving retry without the provider-native
format flag. The retry includes the rejected output, parser or schema error,
and the required schema, including the rule that a nested mapping cannot be
returned as the root object. If both responses are recognizably just a
string-valued nested mapping (for example `player_character_mapping`), Novelist
makes one targeted repair request that retains that mapping and requires the
complete root ledger with source-backed scenes. Other second malformed or
schema-invalid responses fail the run; they are never used as evidence.

Every analysis, verification, writing, and correction request carries its
source-bearing task prompt as a `user` message. Novelist must not submit a
system-only conversation: provider routing may accept such a request while
returning an empty visible completion.

The current source ledger is authoritative. Authority then descends through a
prior session, graph/world context, CharacterAgent guidance, and writing style.
Continuity can affect presentation but cannot add facts. Every ledger claim has
source-segment provenance; absent information must remain absent.

## Writing and verification

Adjacent scenes are grouped deterministically into 1,200–1,800-word blocks and
written sequentially. Each receives the accepted tail of the previous block.
Only `<p>` and optional `<blockquote>` prose is accepted. Code rejects lists,
repeated paragraphs, tiny-paragraph cascades, malformed structure, and density
collapse; one retry is allowed per block.

The merged chapter is verified once against its ledger. If issues identify block
IDs, only those blocks are rewritten, followed by one final verification. A
remaining fidelity issue fails the run rather than returning known-unfaithful
prose. Novelist does not call Architect or Elder, run a literary critic, or make
a mandatory full-chapter rewrite.

## Persistence and compatibility

Run artifacts have `pipeline_version: "v3"` and contain `evidence_ledger`,
`continuity`, `block_plan`, `blocks`, `quality_gate`, `fidelity`, timing, and LLM
usage data. `draft_text` remains final display-ready chapter HTML. The V2
`critic_notes`, scene results, Elder Q&A, and numbered step outputs are
deprecated compatibility fields and are not populated by V3.

Each queued run is linked to its background-job record when the Celery worker
starts. That record provides the queued/running/completed/failed state and
progress updates; failure to create or link the tracking record prevents the
pipeline from starting.

Use `previous_novelist_run_id` to reuse a completed V3 ledger. The older
`previous_session_id` remains a lower-authority graph text lookup.

The last `language` and `instructions` submitted for each Novelist agent are
remembered on that agent. Frontends can retrieve them from `GET /agents/` or
`GET /agents/{agent_id}` in `AgentRead` and prefill the next run form. Omitted
values reuse the saved preference; explicitly empty strings clear it. The resolved
values are attached to the accepted run, so its recorded request remains the
source of truth for the pipeline execution.

See [the endpoint contract](Endpoints/Novelist%20-%20Endpoints.md) and
[pipeline details](Generate_Draft/Generate_Draft.md).
