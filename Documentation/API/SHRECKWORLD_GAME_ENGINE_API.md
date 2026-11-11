# ShreckWorld game engine API (Phase 1)

Base path: `/api/v1/game-engine`. All endpoints require a Shrecknet bearer token. Manager endpoints require `admin` or `world_builder`; ontology-scoped operations also resolve the ontology through Shrecknet using the caller token and fail closed when identity is unavailable. Review and publication are administrator-only. IDs are opaque ShreckWorld UUID strings except `ontology_id`, which remains the Shrecknet integer ID.

Errors use FastAPI's `{ "detail": "safe message" }` envelope. `401` means missing/invalid authentication, `403` role denied, `404` missing/inaccessible scoped resource, `409` idempotency/revision/state conflict, `410` archived table, `422` invalid request/reference, and `503` identity or queue service unavailable. Do not retry non-retryable `4xx` responses without changing the request.

## Rulesets, bindings and immutable versions

| Method and path | Request | Response / behavior |
| --- | --- | --- |
| `POST /rulesets` | `{name, edition, schema_version?}` | `201 RulesetRead`; starts a draft at revision 1. `schema_version` defaults to `1.0`. |
| `GET /rulesets` | — | Manager-visible rulesets ordered by creation. |
| `POST /bindings` | `{ontology_id, ruleset_id}` | `201 BindingRead`; verifies caller can resolve the existing ontology. Duplicate ontology/ruleset binding returns `409`. |
| `GET /bindings?ontology_id={id}` | — | Authorized ontology bindings. |
| `POST /rulesets/{id}/definitions?ontology_id={id}` | `{kind, schema_version, payload, provenance?}` | `201 DefinitionRead`; requires an authorized binding for the ontology. `kind` is `entity_schema`, `content`, `action`, `resolution`, `procedure`, or `progression`. Definitions are accepted only while draft and must use the ruleset schema version. |
| `POST /rulesets/{id}/approve?expected_revision={n}` | — | Administrator-only human review transition from draft to approved. Empty rulesets and stale revisions are rejected. |
| `POST /rulesets/{id}/reject?expected_revision={n}` | `{reason}` | Administrator-only rejection recorded in the review history; the ruleset remains a draft for revision. |
| `GET /rulesets/{id}/reviews` | — | Manager-visible review history with reviewer ID, decision, revision and optional reason. |
| `POST /rulesets/{id}/publish` | `{expected_revision, supersedes_version_id?}` | Administrator-only publication of an approved, non-empty immutable snapshot; returns exact digest and snapshot. |
| `GET /rulesets/{id}/versions` | — | Versions with status and pinned definition snapshot. |

`SourceReference` fields are `source_id`, `document_id`, `document_revision_id`, `content_digest` (64 hexadecimal characters), `locator_kind` (`physical_page`, `printed_page`, `passage`, `section`), and `locator`. Phase 1 validates definition provenance and generation-run source references against persisted ShreckWorld document revisions, the binding ontology, and the requested physical page/printed label/block/section locator. Current structured document IDs are immutable revision IDs; therefore `document_id` and `document_revision_id` must match. Missing, changed, or cross-ontology references return `422` without exposing source content.

Publication requires at least one validated definition. Published snapshots cannot be edited. A successor version records `supersedes_version_id`; tables retain their original version pin. Phase 1 does not execute definition payloads.

## Generation setup runs

| Method and path | Request | Response / behavior |
| --- | --- | --- |
| `POST /rulesets/{id}/runs` | `{idempotency_key, binding_id?, source_refs?}` | `202 RunRead`; stores the run before Celery dispatch. Source refs require an authorized binding. Same owner/key and same request returns the original run; reusing a key with different input returns `409`. |
| `GET /runs?ontology_id={id}&limit=25&cursor=` | — | Owner-scoped run page; ontology filter is optional but validated when present. `limit` is 1–100; cursor ordering is stable by `(created_at, id)`. |
| `GET /runs/{id}` | — | Creator-only run status. Other owners receive `404`. |
| `POST /runs/{id}/retries` | `Idempotency-Key: <key>` header | `202` new attempt linked to the failed/cancelled run; only eligible terminal runs can retry. |
| `POST /runs/{id}/cancel` | — | Cancels queued work or requests cancellation for a running task. |

Phase 1's Celery task is deliberately a placeholder: it records `execution_mode="placeholder"`, `candidates_created=0`, `extraction_performed=false`, and diagnostic code `extraction_not_implemented`. It does not call a model or create rules. The ShreckOS UI must describe this status plainly. Safe errors include `dispatch_failed` and `placeholder_run_failed`; tokens, source excerpts, and raw exception text are not stored as diagnostics.

## ShreckTables

| Method and path | Request | Response / behavior |
| --- | --- | --- |
| `POST /tables` | `{ontology_id, version_id, name}` | `201 TableRead`; verifies current ontology access and requires a published version. |
| `GET /tables?ontology_id={id}&limit=25&cursor=` | — | Authorized ontology-scoped page, stable by `(created_at, id)`; limit 1–100. |
| `GET /tables/{id}?ontology_id={id}` | — | Authorized table with participant reference IDs. |
| `PATCH /tables/{id}?ontology_id={id}` | `{name}` plus `If-Match: <revision>` | Renames an active table and increments revision. Stale revision returns `409`; archived table returns `410`. Ontology and version pin cannot be changed here. |
| `DELETE /tables/{id}?ontology_id={id}` | — | Idempotent soft archive; preserves history and associations, blocks later writes. |
| `PUT /tables/{id}/participants?ontology_id={id}` | `{user_id, role, character_agent_id?}` | Adds one `owner`, `player`, or `observer` reference. The user must exist; a Character Agent must resolve, match the table ontology, and be owned by the associated participant. Duplicate association returns `409`. |
| `DELETE /tables/{id}/participants/{user_id}?ontology_id={id}` | — | Removes the association; archived tables reject changes. |

Tables store one integer ontology reference and one immutable published version ID. No cross-database foreign key is created. Ontology checks run server-side on every scoped request; frontend visibility is not an authorization boundary. Participant links do not grant action authority. Game time/state, embodiments, event execution, delegated Companion authority, and Foundry synchronization are not implemented in Phase 1.

## Compatibility and operations

New SQL tables are created additively by ShreckWorld's existing `create_schema` startup path. Existing `shreckworlds`, library items, source routes, read-only rule review, and ontology IDs are unchanged. The current ShreckWorld database and media are included in the existing `shreckworlds` backup category; restore should retain registry rows and must not reconcile dangling references by display name. Deploy ShreckWorld API/worker before the ShreckOS UI. Rollback may hide the app/disable new routes but must retain persisted drafts, runs, versions, and tables.

See [ShreckWorld architecture](../Architecture/SHRECKWORLD.md) for service ownership and [backup operations](../Deployment/BACKUPS.md) for the bundle contract.
