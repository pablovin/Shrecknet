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
| GET | `/sources` | Explanatory sources only, including learning states |
| POST | `/learn` | Queue background discovery and pin current documents |
| GET | `/runs` | Most recent ontology discovery runs |
| GET | `/runs/{id}` | Status, stage, detailed progress, activity log, proposals and diagnostics |
| POST | `/runs/{id}/cancel` | Request cancellation |
| PATCH | `/runs/{id}/proposals/{proposal_id}` | Human correction of proposed schema |
| POST | `/runs/{id}/review` | Explicitly accept/reject every proposal |
| GET | `/ontology` | World-local definition graph |
| GET | `/revisions` | Historical graph snapshots |
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
  "source_ids": ["SOURCE_UUID_A", "SOURCE_UUID_B"]
}
```

The server verifies all selected Sources are **explanatory** and have a
current active Bookmap. It pins document IDs and content digests before queueing.
The task reads Bookmap V3/V4 and persisted blocks without re-indexing.
Combined entity/schema discovery and a separate reconciliation model produce evidence-backed proposals. Source blocks are visited once, grouped by Bookmap chapter, with subsection headings retained. Long chapters are divided into bounded, non-overlapping ~24,000-character windows. Text blocks beyond one window are fragmented without truncation.
The task never writes approved entity definitions.

Runs advance through `queued`, `running`, `awaiting_review`, and
`succeeded`, or `cancelled`/`failed` with safe diagnostics.
A queued cancellation is terminal immediately; a running job changes to
`cancel_requested` and stops cooperatively at the next model-call checkpoint.
An in-flight model request cannot be interrupted mid-response.

`/runs`, `/runs/{id}`, and the initial `POST /learn` result include
`activity`, `activity_log`, and `model_summary`. Activity events give their
timestamp, stage, source/chapter names, source number, window number,
completed/total windows, discovered proposals, and reconciliation progress.
These fields are persisted in the existing bounded run diagnostics JSON
(250 recent events), with user-facing progress events separated from error
diagnostics by the API. No database migration or new tables are necessary;
runs created by previous versions return empty activity records. Model
summaries include provider/model name but omit credentials.

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
