# Shreckworld identity and shared table knowledge

Shrecknet owns the string world ID and world metadata. A world has one active
ontology scope. Atlas, Sources, the Game Engine, Ontology Studio, Character Agents
and existing jobs use that world's integer ontology ID at their storage boundary.
The same integer identifies the Neo4j partition, source library and document
index for every table in the world. A table owns its roster, scheduled sessions,
integrations, private attachments and attendance.

## Core API

| Request | Result | Authorization |
| --- | --- | --- |
| `GET /worlds` | World directory | Authenticated |
| `GET /worlds/{world_id}` and `GET /contracts/worlds/{world_id}` | World metadata, `ontology_ids` and `active_ontology_id` | Authenticated |
| `POST /worlds` | Create a world and empty ontology in one SQL transaction | World builder or admin |
| `PATCH /worlds/{world_id}` | Update supplied metadata | World builder or admin |

World metadata includes `name`, `description`, `image_url`, `rpg_system`,
`rpg_edition` and `rpg_subversion`. Optional fields can be cleared with JSON
null. A missing world returns 404. An unresolved world has a null
`active_ontology_id` and lists its legacy ontology IDs. Existing ontology
responses include `world_id`. The legacy ontology creation route and starter
setup create a world automatically. Compatibility metadata fields are updated
together when a world has one ontology. Deleting a world-owned ontology through
`DELETE /ontologies/{ontology_id}` returns 409 because the identity is retained
by graph, source and table records.

## ShreckWorld API

| Request | Result |
| --- | --- |
| `POST /api/v1/shrecktables/tables` | Creates a table in the supplied `world_id` using the active ontology. Compatibility `ontology_id` and `version_id` are accepted when they match the world. |
| `GET /api/v1/shrecktables/tables?world_id={world_id}` | Lists visible active tables in that world. The legacy `ontology_id` filter remains available. |
| `GET /api/v1/shrecktables/tables/{table_id}/world` | Resolves canonical graph and source scope plus the shared rules selection. Requires access to the table. |
| `GET /api/v1/game-engine/worlds/{world_id}/rules` | Returns `version_id`, `revision` and `legacy_conflict`. |
| `PUT /api/v1/game-engine/worlds/{world_id}/rules` | World builder or admin sets a published version bound to the active ontology, or clears it with `version_id: null`. Requires `expected_revision`. Every table subsequently reads the same selection. |
| `POST /api/v1/shrecktables/tables/{table_id}/sessions/{session_id}/narrative-source` | A table manager promotes a reviewed account via `title` and `narrative`, with optional `attachment_id`. Returns the world, ontology and new source IDs. |

A mismatched or unresolved world scope, incompatible rules choice, stale rules
revision or repeated session promotion returns 409. An attachment must belong
to the source session. Session reads expose the promoted `narrative_source_id`.
Promotion creates an authored Markdown source in the shared narrative library.
The attachment remains table-private. Its ID is retained in the provenance
record and the reviewed text is visible to world readers. Existing Architect
workflows can process that source under the world's ontology and retain
source, section and paragraph provenance when adding graph entries.

## Migration and rollback

Core startup adds metadata columns and backfills orphaned ontologies into
deterministic worlds while preserving existing world and ontology IDs. Existing
worlds with one ontology inherit its absent columns on schema upgrade. Multiple
legacy ontologies owned by one world are logged and their IDs are preserved.
New table creation and session promotion need a single resolved scope.
Reconcile graph, source and table references together before resolving an
ambiguous legacy world.

ShreckWorld adds world rules selections and promotion provenance to its SQL
database. In the absence of an explicit world selection, all tables see the
same compatible historical pin when one exists. Divergent or unresolvable pins
are surfaced together as `legacy_conflict`. Select a published world version
in Game Engine to resolve them. Per-table pins are retained for migration
forensics. The legacy Game Engine table creation endpoint returns 410.
Migrated and independently retained UUID records can still be accessed through
their existing legacy read routes.

The upgrade preserves existing Neo4j IDs, vector namespaces, files, document
revisions, links and Google event IDs. Deploy Core first, then ShreckWorld and
ShreckOS. Validate on a copy of the SQL databases and existing graph, vector and
media stores before production cutover. Back up all stores together so a rollback
can restore the same set of IDs and source revisions.
