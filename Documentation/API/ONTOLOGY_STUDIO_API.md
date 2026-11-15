# Ontology Studio — Learn Ontology (backend API)

Status: Ontology Studio backend is merged into Shrecknet main. ShreckOS Apps frontend resides in `pablovin/ShreckRPG/shreckos`.

## Ownership and isolation

ShreckWorld owns all `shreckworld_ontology*` definitions, edits, revisions,
source-learning history, and Celery jobs. Shrecknet Core remains authoritative
for the integer `ontology_id`, authentication, world access, and existing
legacy ontology/Neo4j data. No Ontology Studio API mutates the legacy graph.

Use an authenticated world-manager token with access to the selected world.
API prefix: `/api/v1/ontology-studio/worlds/{ontology_id}`.

## Configure the Architect

Shrecknet Setup exposes the Architect ontology targets. The active extraction pipeline uses **two model calls types**, not three consecutive readings:

- `model_architect_ontology_discovery`: **combined discovery + schema extraction** per Bookmap chapter/window (entity types, typed properties, relationships and evidence in one response).
- `model_architect_ontology_reconciliation`: normalize and compare proposed definitions with the existing ontology.
- `model_architect_ontology_schema`: retained as a compatibility setting for existing configurations; the active combined pipeline does not invoke it.

Each choice uses the normal target contract: `provider`, `name`,
`reasoning`, `reasoning_effort`, and `web_search_enabled`. Defaults use
OpenRouter DeepSeek v4.1 Flash. Values persist in the Shrecknet configuration
database and are atomically projected into the shared non-secret
`service-config/shreckworld.json`. ShreckWorld reads this projection.
A new run captures the three resolved model targets; changing Setup does not
silently change a run already queued.

The current extraction prompts are source-grounded. No web retrieval or
Neo4j mutations are performed by Learn Ontology.

## API endpoints

| Method | Route suffix | Purpose |
| --- | --- | --- |
| GET | `/sources` | Explanatory sources, learning states, active Book Map chapters, and per-document chapter sent state |
| POST | `/learn` | Queue background discovery for selected chapters and pin current documents |
| GET | `/runs` | Most recent ontology discovery runs |
| GET | `/runs/{id}` | Status, stage, detailed progress, activity log, proposals and diagnostics |
| DELETE | `/runs/{id}` | Delete a terminal run and its proposal history; clear chapter checkpoints for a full re-extraction |
| GET | `/runs/{id}/windows` | Detailed extraction-window checkpoints |
| POST | `/runs/{id}/recover-proposals` | Restore an empty review from saved extraction-window candidates |
| POST | `/runs/{id}/discard-proposals` | Undo the latest approved run revision and reopen its proposals |
| POST | `/runs/{id}/cancel` | Request cancellation |
| PATCH | `/runs/{id}/proposals/{proposal_id}` | Human correction of proposed schema |
| POST | `/runs/{id}/review` | Explicitly accept/reject every proposal |
| GET | `/ontology` | World-local definition graph |
| GET | `/revisions` | Historical graph snapshots |
| GET | `/entities/{entity_id}/instance-template` | Preview world-scoped instance value schema |
| POST/PATCH/DELETE | `/entities[/id]` | Entity CRUD |
| POST | `/entities/{id}/properties` | Add typed property |
| PATCH/DELETE | `/properties/{id}` | Property edits |
| POST/PATCH/DELETE | `/relationships[/id]` | Directed edge CRUD |

All graph mutations take `expected_revision`; stale writes return HTTP 409.
For a world that has no Ontology Studio definition graph, the first read returns
`revision: 0`. Every successful mutation creates a new immutable revision.
IDs are UUIDs, not display names.

### Example: request learning

```json
{
  "idempotency_key": "world-7-rulebooks-2026-11-14",
  "source_ids": ["SOURCE_UUID_A", "SOURCE_UUID_B"],
  "chapter_ids": {"SOURCE_UUID_A": ["BOOKMAP_CHAPTER_ID"]}
}
```

`chapter_ids` is optional for compatibility. Omitted source entries mean all
chapters with indexed text in that source's active Book Map. The ShreckOS
chapter picker defaults to all chapters and can submit a subset. `/sources`
returns each source's chapter `id`, `title`, and `sent` flag. A chapter is shown as sent only after every window under the active
`ontology-schema-v5` contract completed successfully in the same run, for the
active document digest. Legacy extraction checkpoints do not count as V5 extraction, and
changing the document digest marks its chapters unsent. Window checkpoints are
stored in the additive ShreckWorld table.

The server verifies all selected Sources are **explanatory** and have a
current active Bookmap. It pins document IDs and content digests before queueing.
The task reads Bookmap V3/V4 and persisted blocks without re-indexing.
Combined entity/schema discovery and a separate reconciliation model produce evidence-backed proposals. Source blocks are visited once, grouped by Bookmap chapter, with subsection headings retained. Long chapters are divided into bounded, non-overlapping ~3,000-token target windows. Text blocks beyond one window are fragmented without truncation.
The task never writes approved entity definitions.

### V5 world-local semantic ontology extraction

This additive contract supersedes V3. **Every ontology belongs to its Shrecknet
world** (`ontology_id`). The extractor may use cross-game examples to learn
classification patterns but must never import concepts or semantics from a
different world. The approved SQLite graph remains the source of truth.

The first pass discovers reusable entity types, their **named properties**
(including named skills and characteristics), semantic types, property metadata
and instance value-field schemas. It does not create a character, spell, item,
occupation catalog record, or executable game rule. Any entity type — not only
characters — can own a stateful property.

Example compact discovery output (fragment):

```json
{
  "e": [{
    "n": "Investigator", "d": "A playable character", "b": [0],
    "p": [{
      "n": "spot_hidden", "t": "i", "s": "Skill", "d": "Noticing hidden details",
      "m": [{"n": "base_chance", "t": "i", "v": "25"}],
      "i": [
        {"n": "current_value", "t": "i", "r": true},
        {"n": "improvement_mark", "t": "b", "r": false}
      ],
      "b": [1]
    }], "r": []
  }]}
}
```

- `s` is a source-grounded, **world-local** semantic type (Skill,
  Characteristic, Passion, Rune, Resource, etc.). The set is open, not global.
- `m` provides evidence-backed, typed *definition* metadata: base chance,
  associated characteristic, bounded ranges, and specialized descriptions.
- `i` defines fields each implementing instance receives. All properties
  receive a required but initially nullable `current_value` slot with a type
  matching the property. Extra state fields are discovered as needed.
- `d` provides source-grounded concise descriptions. Provenance is
  reconstituted from numbered Bookmap blocks and remains available for review.
- Character-specific scores belong to instances, not to the shared definition.
  Derived statistics and formulas are deferred to the rules-extraction phase.

### Partial output and validation diagnostics

Each extraction window uses one discovery model request. The response schema
contains only the `e` entity list; there is no overflow field, preflight, or
recursive source splitting. The model is asked to return as many complete,
source-grounded definitions as fit.

The worker salvages complete entity objects from syntactically truncated JSON.
Within parsed entities, it validates entity types, properties, relationships,
and metadata independently. A malformed part is skipped while valid siblings
are retained. Each window stores its `validation_diagnostics`, including the
part path and validation code. `GET /runs/{run_id}/windows` returns these
diagnostics in each window's `validation_diagnostics` field, and the run
activity log includes a bounded summary for the completed window. ShreckWorld
adds this JSON field to existing window tables during its normal schema startup
migration. V4 checkpoints are retained but are not reused as V5 results. A
window with valid partial results completes and remains
available for review; a response with no recoverable complete entities also
completes with diagnostics so the overall job can proceed.

Unparseable scalar metadata is preserved as source-grounded text and flagged
for human review. Fatal provider refusal or transport errors still fail the
window. The workflow uses one combined discovery/schema model call per input
window, followed by semantic reconciliation of plausible duplicates. Alias
merges preserve evidence; incompatible types or definitions require explicit
human review. Approved definitions are never silently retyped.

Existing property CRUD accepts `semantic_type`, `definition` and
`value_schema`; these fields also appear in the graph and proposal review
APIs. Legacy property rows migrate additively, with no old values discarded.
`GET /entities/{entity_id}/instance-template` previews the fields of an
instance including inherited properties (all values begin as `null`).
It does not yet persist real gameplay instances or execute learned rules.


Runs advance through `queued`, `running`, `awaiting_review`, and
`succeeded`, or `cancelled`/`failed` with safe diagnostics.
A queued cancellation is terminal immediately; a running job changes to
`cancel_requested` and stops cooperatively at the next model-call checkpoint.
An in-flight model request cannot be interrupted mid-response.

`/runs`, `/runs/{id}`, and the initial `POST /learn` result include
`activity`, `activity_log`, and `model_summary`, plus cumulative `input_tokens`,
`output_tokens`, and `llm_calls`. Token totals are provider-reported prompt and
completion usage from successful discovery and reconciliation responses. When a
provider omits usage, the call is counted and its unavailable token values add
zero. Failed model requests are not counted as completed calls. Activity events give their
timestamp, stage, source/chapter names, source number, window number,
completed/total windows, discovered proposals, typed schema counts, suppressed example instances, unresolved relationship targets, and reconciliation progress.
Activity fields are persisted in the existing bounded run diagnostics JSON
(250 recent events), with user-facing progress events separated from error
diagnostics by the API. Usage totals are persisted on each run. Existing SQLite
databases add the three integer columns at startup with zero defaults; prior runs
therefore remain readable and report zero usage. Model summaries include
provider/model name but omit credentials.

The worker saves the consolidated proposal JSON before its final progress
checkpoint. The checkpoint refreshes only cancellation status, preserving
pending proposal writes. For older runs whose final proposal list was lost but
whose completed window outputs remain, `POST /runs/{id}/recover-proposals`
reconstructs and deduplicates the validated window candidates and returns the
run to `awaiting_review`. This recovery does not repeat model reconciliation;
the response includes a safe warning so candidates are reviewed carefully.
Recovery is available only when the run has no final proposals, saved completed
window candidates, and no ontology revision newer than the empty review result.

`POST /runs/{id}/discard-proposals` reopens a succeeded run for review. It
restores the ontology snapshot from before that run's approval, clears prior
accept/reject decisions, and records a new immutable revision for the restore.
The operation is rejected with 409 if the ontology has changed since approval,
so later edits cannot be silently removed. The run records `applied_revision`;
existing databases receive this nullable integer column additively at startup.

`DELETE /runs/{id}` removes a terminal run, its proposals, and source-learning history tied to that run. It also removes all extraction-window checkpoints for the run’s source, document digest, and chapter, plus same-source checkpoints with matching content fingerprints that could otherwise satisfy cache lookup. This clears the chapter `Sent before` state and guarantees the next run sends those matching windows to the discovery model again. A run is not deletable while it is queued, running, or cancellation-requested; deletion also returns 409 if another active run includes any of its chapters. The endpoint does not undo schema changes already applied by a successful review; use `POST /runs/{id}/discard-proposals` before deletion when those changes should be reverted. The response reports `deleted_runs`, `cleared_checkpoints`, and `cleared_chapters`.

 The user
must review all proposals before successful adoption.

### Example: review

```json
{
  "expected_revision": 3,
  "decisions": {
    "PROPOSAL_UUID_A": "accept",
    "PROPOSAL_UUID_B": "reject"
  }
}
```

A review merges accepted entity types, typed properties, and relationships into
one revision in dependency order. Existing properties cannot be silently
retyped; a conflicting type is rejected with 409 and must be edited explicitly.
Existing human-authored descriptions are not overwritten. Accepted/rejected
decisions are recorded in the original run proposal list.

Source learning history is recorded after reviewed application. `/sources`
compares the learned digest with the active document digest and shows `ready`,
`learned`, `outdated` or `not_indexed`, along with `last_learned_at`.

## ShreckOS frontend integration contract

Add one **Ontology Studio** entry to ShreckOS Apps, gated to authorized
world-managers. Use three internal views:

1. **Learn:** world picker; Explanatory Source card shelf; states, last learned,
   multiselect and background job progress; reviews with source evidence.
2. **Explore & Edit:** React Flow canvas with drag/pan/zoom/layout, auto layout,
   entity inspector, typed property editor, connect-to-create relationship
   edges, delete confirmations, filters and source provenance reader.
3. **History:** run results and ontology revision history.

On mobile, replace the persistent inspector with a bottom sheet and keep the
entity list as an accessible navigation alternative. Expose 409 conflicts as
refresh-and-retry actions. Do not map extraction candidates directly to
graph instances; this first stage defines schema only.

## Deployment and testing

If `/api/v1/ontology-studio/worlds/1/sources` returns **404**, first verify
that ShreckWorld API and worker containers have been rebuilt from a current
Shrecknet checkout. The route is registered in `app.main`; an old image will
still respond 404 even if ShreckOS is already updated. From the Shrecknet
checkout run `docker compose build shreckworld_api` and
`docker compose up -d --no-deps --force-recreate shreckworld_api shreckworld_worker`.
The source list is available only to authenticated world managers; distinguish
401/403 authorization failures from 404 router absence and 500 server errors.

Deploy Shrecknet config publisher and `shreckconfig` before ShreckWorld
API/workers to ensure model targets are present. New SQL tables are additive,
imported in `app.models`, and created by existing ShreckWorld schema setup.
Backups should retain the existing ShreckWorld SQLite data. Legacy
Shrecknet/Core models and graph instances remain unchanged.

Run `cd shreckworld && pytest tests/test_ontology_studio.py`, then the
full ShreckWorld suite. This change does not include the separate
ShreckOS frontend repository or its production build.
