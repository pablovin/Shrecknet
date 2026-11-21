# Sources Workspace API (first implementation slice)

The ShreckWorld service owns Sources (formerly library items), reusable hierarchy templates,
editorial structure revisions, and document generation references. Shrecknet owns ontology
identity, authentication, and world authorization. ShreckOS owns the editor.

## Ownership

All workspace routes are under `/ontologies/{ontology_id}`. The caller must be
authenticated and authorized to read the referenced ontology by Shrecknet.
Mutating routes require the existing `require_world_manager` role check.

No endpoint in this slice writes Neo4j nodes, ontology definitions, scenes, milestones,
or existing document generations. Hierarchy deletion may remove source-owned database
records, stored files, and rebuildable retrieval vectors through the source lifecycle.
The original PDF's Book Map remains immutable and independently retrievable unless its
owning source document is explicitly deleted with its hierarchy.

## Contracts

- `GET /source-hierarchies`: latest version per hierarchy, including archived.
- `GET /source-hierarchies/{id}/versions/{version}`: version-pinned template.
- `POST /source-hierarchies`: create from `HierarchyInput`.
- `POST /source-hierarchies/{id}/image?expected_version=N`: upload a PNG, JPEG or WebP image (maximum 5 MB), appending a version.
- `GET /source-hierarchies/{id}/versions/{version}/image`: authenticated, version-pinned image read.
- `PUT /source-hierarchies/{id}`: append a new version, requires
  `expected_version` of the current version (409 on conflict).
- `DELETE /source-hierarchies/{id}?expected_version=N&remove_items=true`: permanently removes the hierarchy assignment and every source document in that ontology that uses the hierarchy. Source file storage, structured source rows, and retrieval projections follow the normal source deletion lifecycle. Hierarchy template versions and artwork are hard-deleted only when no other ontology collection or source references the shared template. The default `remove_items=false` retains the legacy archive-only behavior.
- `POST /sources`: create an authored Source and atomically instantiate a starter outline from its pinned template levels, with required `title`,
  `source_type` (narrative or explanatory), `hierarchy_template_id`,
  `hierarchy_template_version`. Reuses the existing library-item ID space.
- `PUT /sources/{source_id}/settings`: type/title/template revision with
  `expected_revision` (409 on conflict).
- `GET /sources/{source_id}/organization`: editable projected or saved
  section tree and current Source revision.
- `PUT /sources/{source_id}/organization`: save a reviewed tree with
  `expected_revision` and complete `nodes`.

All paths above share the `/ontologies/{ontology_id}` prefix.

The previous `/ontologies/{ontology_id}/items` and PDF reader/index/job routes
remain unchanged. Existing PDF library items are backfilled to Explanatory/Rulebook
without recreating vectors or documents. The migration seeds separate RPG Campaign
and Novel templates; each retains immutable versions.

## Section structure

`SourceSection` has stable `id`, nullable `parent_id`, `section_type`,
`title`, sibling `order`, typed `content_reference`, and `metadata`.
One root is required and its ID is `source-{source_id}`. Parents must exist,
cycles and sibling-order collisions are rejected, and authored paragraph IDs must
remain unique within a source. A section may own paragraphs and contain children.
Each authored paragraph accepts up to 10,000,000 characters. The complete authored
organization is limited to 10 MB of UTF-8 text; graph-imported entity Markdown
uses the same 10 MB limit per Source.

`content_reference` is either:

- `{ "kind": "authored", "paragraphs": [{"id":"p1","text":"..."}] }`
- `{ "kind": "extracted", "document_id": "...", "node_id": "...",
  "source_sha256": "<64-hex-digest>" }`

Extracted references are validated against the owning Source's retained document
generations and Book Map nodes. The service does not modify or erase original
extracted text. V3 and V4 Book Maps remain in their original formats. Section
projections use stable UUIDv5 IDs derived from Source/document/Book Map identity.
A stale editor save fails rather than silently overwriting concurrent changes.

Section removal is editorial. The old complete tree revision remains in
`source_organizations`; original Book Map nodes and vectors are not deleted.
Deleting a hierarchy with `remove_items=true` is a separate destructive operation:
it deletes the ontology-owned source documents associated with that hierarchy,
including their stored files and structured source projections. A shared template
remains available to other ontologies until its last collection and source reference
is removed.

## Narrative and Explanatory files

The existing upload API `POST /ontologies/{ontology_id}/items` accepts the
multipart field `pdf` (legacy field name) for PDF, TXT, HTML/HTM, DOCX and MD
uploads. It accepts the additional form fields `source_type` (`narrative`
or `explanatory`), `hierarchy_template_id`, and
`hierarchy_template_version`. Older callers default to
`explanatory`/`rulebook`/version 1; they retain their original storage and
indexing behavior. Templates must exist and be active at upload time.
HTML uploads run through Docling's document conversion like DOCX and Markdown;
they are not executed as browser code.

`POST /ontologies/{ontology_id}/sources` creates an authored source for
**either** source type. Its original file is a UTF-8 `.md` document under
the ontology's protected media directory. Template levels are materialized
as initial headings in that document. The source has an actual
`storage_path`, `file_size`, `source_sha256`, `mime_type=text/markdown`
and downloadable original file from the existing authorized content route.
It no longer uses a fabricated empty PDF path.

Saving an authored organization writes a new immutable physical Markdown
file before atomically changing the source database revision, content digest
and active file pointer. The previous file revision is retained for recovery
as long as its source is retained. A failed commit discards the newly
staged file, not the previous one. A version conflict returns HTTP 409.
The organizer remains the authoritative structure: the physical file is a
materialized snapshot, not a second editable owner.

Existing authored records created before this change may have an empty
original-file path. Run a dry run from `shreckworld/`:

```bash
python scripts/backfill_authored_markdown.py
python scripts/backfill_authored_markdown.py --apply
```

The second command is intentionally explicit. It materializes the latest saved
organization as a protected Markdown original while preserving its Source ID,
organization history and revision number. It skips records without a verified
ontology owner. Run it after coordinating a database/media backup; older
authored source rows are otherwise still readable through the organization API.

The shared rich-document editor stores sanitized HTML fragments in
`AuthoredContent.paragraphs`. Raw HTML blocks are legal Markdown, allowing
tables, headings and formatted text to retain their appearance without a
lossy round trip. These embedded fragments must be sanitized before
browser rendering; the source serves them only as a protected text file.
Image uploading within authored rich text is disabled until an owned source
media API exists.

## Sources V2: category hierarchies and entries

The primary Sources management workflow is now category-first. World-local
**SourceCollection** rows assign reusable `SourceHierarchy` template versions to
either **Narrative** or **Explanatory**, without creating or rewriting documents.
A template can have one collection per ontology/category; reassigning an archived
collection restores its previous entries and pinned version.

Required ontology-scoped routes:

- `GET /source-collections?category=narrative|explanatory`: list only the chosen
  category, including archived assignments. There is no mixed-category endpoint.
- `POST /source-collections`: assign an active template to the category,
  using `source_type`, `hierarchy_template_id` and
  `hierarchy_template_version`.
- `GET /source-collections/{collection_id}`: read a single collection.
- `PUT /source-collections/{collection_id}`: atomically save name,
  description, archive flag and all entries with `expected_revision`.
- `PUT /source-collections/{collection_id}/template` with
  `expected_revision` and `template_version`: explicitly upgrade the
  pinned hierarchy schema after confirming its new level names. Existing
  entry IDs, attachments and hierarchy positions are preserved; shortening
  below an occupied level is rejected with HTTP 422.
- `DELETE /source-collections/{collection_id}?expected_revision=N`:
  archive an assignment; **not** the template or linked documents.
- `DELETE /source-collections/{collection_id}/assignment?expected_revision=N`:
  permanently remove the hierarchy assignment and its entries/artwork from this
  ontology while retaining attached source documents in the world library as
  unassociated sources. The reusable template remains available for assignment
  again. A stale revision returns HTTP 409.
- `POST /source-collections/{id}/entries/{entry_id}/image?expected_revision=N`:
  upload an authenticated PNG, JPEG or WebP (maximum 5 MB).
- `DELETE /source-collections/{id}/entries/{entry_id}/image?expected_revision=N`:
  detach the artwork while keeping older media for recovery.
- `GET /source-collections/{id}/entries/{entry_id}/image`:
  authenticated image retrieval.

All paths above share the prefix `/ontologies/{ontology_id}`.
Mutations require the existing Sources administrator role and the caller must
have access to the ontology.

The additive startup migration also seeds **Campaign Adventures**
(`Adventure → Story Arc → Session`) and **World Lore**
(`Topic → Entry → Section`) templates, plus a **Markdown Document**
(`Document`) template used by default when creating attached Markdown files without changing prior custom
templates, their versions, or existing source documents.

Each entry stores a stable ID, title, optional description, parent ID,
hierarchy level, sibling order, optional protected artwork key and
`source_ids` attachments. Narrative entry responses also include the
read-only `graph_source_entity_ids` projection resolved from imported source
items. This field is ignored on writes. ShreckOS can use those exact graph
entity IDs with Core's authorized batch count endpoint and the
`GET /ontologies/{ontology_id}/scenes` `source_entity_id` filter to show scenes
derived from each source item. Items
without graph-import mappings return an empty array. The API validates strict hierarchy levels,
no cycles, sibling-order uniqueness, artwork ownership and document
references. Attachments must belong to **the same ontology and the same
Narrative/Explanatory category**. Both Narrative and Explanatory entries can
have at most one attached source; a `PUT` that adds another returns HTTP 422.
Existing entries with excess attachments remain readable and editable so their
old references can be removed; adding a new source is rejected until the entry
has one or none. Graph-source import preserves all provenance mappings while
projecting no more than one source onto each hierarchy entry. Drag/reparent
operations are saved as one revision. A stale edit returns HTTP 409 rather
than overwriting another person's changes.
Entry `id` and `parent_id` values must contain 1–128 ASCII letters, digits,
underscores, or hyphens. Invalid IDs return HTTP 422; this restriction keeps
artwork paths within the collection's protected media directory.

**Collections do not own documents.** Removing an entry or an entire
collection detaches references but leaves the original files, Book Maps,
document indexes, jobs and history untouched. Existing documents are
shown in their category as Unassigned until attached to entries. Earlier
`SourceOrganization` content remains in its original per-document
revision store; this change does not silently migrate it into
SourceCollection or create a duplicate entry hierarchy.

## Sources V2: document reading and rich Markdown editing

- `GET /sources/{source_id}/document`: bounded preview of TXT, HTML, DOCX
  and Markdown originals; uploaded and authored Markdown are editable unless
  truncated. Markdown previews and edits support up to 10 MB. PDF returns an indicator so the
  authorized original-byte streaming endpoint can be used for the browser
  PDF viewer.
- `PUT /sources/{source_id}/document`: revision-safe rich editing for
  uploaded and authored Markdown. It writes a new immutable Markdown file
  version with a new source checksum and marks its existing index stale.
  For authored sources, the service also updates the `SourceOrganization`
  projection from the editor's heading tree and rich-text blocks in the same
  revision transaction. Previously indexed generations are retained and are
  not passed off as current. The Sources page exposes this through explicit
  Edit, Save and Cancel actions and the shared Chronicle rich-text editor,
  including source-owned image uploads.
- Imported TXT, DOCX, HTML and PDF stay read-only. DOCX previews are
  bounded and display plain text, without running macros or loading
  external assets.

To deploy: create the new `source_collections` table via the existing
`Base.metadata.create_all` startup path (the model is imported in
`app.models`), then upgrade ShreckOS. Existing source tables are
unmodified. A rollback can hide the new V2 screen and leave the additive
collection table and documents intact. Back up the SQL database and media
together before upgrades.

## Current boundaries

- Authored Markdown is persisted as real files but is **not yet** indexed
  into full-text or semantic retrieval. Narrative source indexing is a separate
  integration step; until then it is not queryable by Elder/ShreckWorld.
- Hierarchy template images are stored in protected media with version-pinned authenticated reads and an administrator-only upload endpoint. Image uploads append a template version.
- The original Sources workspace does not change Story/Adventure graph nodes, scene
  provenance, or the Architect. The automatic startup importer described below
  **copies** scene-derived narrative material into Sources without altering the graph;
  its read-only mappings let clients query canonical scenes without title matching.
- Older source routes and SDK consumers remain supported.

## Deployment and rollback

Deploy the ShreckWorld backend first, then the corresponding ShreckOS UI.
`create_schema()` applies additive source columns and template seeds.
Back up the database and media before deployment. Rollback can disable the
new UI and restore an earlier binary while retaining additive tables/columns;
do not delete newly authored records until they are exported or backed up.
Inspect legacy ontology ownership before a full storage rollback.

Run `pytest tests/test_sources_workspace.py` from `shreckworld/` and the
frontend test/lint/build commands from `shreckos/` before enabling in production.

## Automatic graph narrative source import (startup)

The **insert-only, read-only-from-graph** importer runs automatically on every
ShreckWorld API startup after its own SQL schema is initialized. Its default is
**enabled**. Set `SHRECKWORLD_GRAPH_IMPORT_ON_STARTUP=false` only when an
administrator intentionally needs to disable imports.
The startup task runs in a daemon thread with bounded connection retries
(up to 12 attempts at ten-second intervals); failures are logged without
blocking normal service startup. Reboot to retry an unavailable
Neo4j connection or a previously failed group. Concurrent importer processes
use a file lock under the ShreckWorld media directory.
Docker Compose starts the API when model prefetch starts instead of requiring
prefetch to complete successfully. This lets the graph import run even when
the semantic model is unavailable; `/ready` can still report semantic
readiness failures independently.
Failed source groups are logged with their ontology, instance, root entity,
hierarchy signature and exception traceback to help diagnose import errors.

Docker Compose mounts two inputs **read-only** for the importer:
`/legacy-shrecknet-data/shrecknet.db` (SQL ontology definitions) and
`/legacy-shrecknet-media` (original local image media). The ShreckWorld
service accesses Neo4j using its existing Shrecknet connection credentials from
`configs/neo4j.env`. Optional overrides are
`SHRECKWORLD_LEGACY_ONTOLOGY_DATABASE_PATH`,
`SHRECKWORLD_LEGACY_MEDIA_ROOT`, and
`SHRECKWORLD_GRAPH_NEO4J_URI/USER/PASSWORD/DATABASE`.
Do not mount the legacy SQLite database read-write.

On every API startup, the importer enumerates all ontology IDs represented by
Neo4j `EntityInstance` nodes. It loads the graph entities and typed
`EntityInstance -[:RELATES_TO]-> EntityInstance` relationships for each one,
then uses the SQL `entity_relationships` definitions to reconstruct complete
ancestry across ontology pages. An ontology is eligible only when a Scene has
a `DERIVED_FROM` entity and that entity resolves to a complete narrative tree.
The importer recognizes Adventure roots and terminal Story, Session, Timeline,
Chapter, Episode, and localized equivalents such as `Aventuras` and `Estórias`.
Intermediate entity types are retained from the graph path without requiring a
fixed name or an exact depth, up to 16 levels. Examples include
`Adventures → Story Arcs → Stories`, `Adventures → Years → Timeline`,
`Adventures → Years → Volumes → Timeline`, and
`Aventuras → Anos → Arcos → Estórias`. A terminal type without an Adventure
ancestor does not create a hierarchy by itself. Ambiguous shortest paths are
skipped and logged.
Startup logs report ontology counts and distinguish worlds without Scene
anchors from those whose anchors do not resolve to a supported hierarchy.

Each ontology gets one Narrative source hierarchy collection for each
recognized graph type signature. Collection entries are graph entities
with stable parent/child links, display names, descriptions and copied local
images. A Scene anchoring a node beneath an Adventure selects the complete
Adventure tree: all related lower-level entities are included, even when an
individual Story or Timeline node has no Scene. Existing manually created collections such as `RPG Campaign`
are preserved. Earlier malformed one-level templates named `Imported: Stories`
and `Imported: Timeline` are archived when the importer runs.

Each imported Adventure, Story Arc, Story or Timeline with graph `text`,
`autogenerated_text`, a description, or a Scene anchor has an ontology-owned
Markdown source attached to its matching collection entry. When authored and
generated text differ, both are included in the Markdown document. These
sources remain unindexed until the normal indexing workflow runs. The importer
does not write to Neo4j or legacy SQLite. Only Shrecknet-owned `/media/` image
paths inside the read-only legacy media directory are copied; external URLs
are never fetched. Missing or unsupported images do not prevent text import.
An entity Markdown document larger than 10 MB causes its hierarchy group to be
logged and skipped, so startup does not partially commit that collection.

Section asset downloads use:
`GET /ontologies/{ontology_id}/sources/{source_id}/assets/{asset_id}`.
This endpoint authorizes the source owner and verifies the asset still
belongs to the section. Markdown is downloaded as an attachment, images
may display inline, and no file paths are exposed publicly. Updating a
Source organization with existing section asset IDs produces new immutable
Markdown file revisions for any edited section paragraphs. Retained versions
and the organization revision remain recoverable. Image upload/editing in the
Sources UI is not changed by the importer.

Identity/idempotency is enforced in `graph_source_imports` with a unique key
`(ontology_id, instance_id, entity_instance_id)` and deterministic collection
entry IDs. Every startup checks all discovered ontology IDs again; existing
entries and documents are reused, while newly eligible graph entities are
added without duplication. User-edited collection entries and documents are
preserved. Deleted or renamed graph nodes are not automatically removed, and
edits to already imported graph text are not synced. Ambiguous ancestry and
invalid hierarchy trees are logged with tracebacks and skipped. The import is
intentionally **not** a graph migration or a semantic-indexing job.

To disable the import for a deployment:
```bash
SHRECKWORLD_GRAPH_IMPORT_ON_STARTUP=false docker compose up -d --build shreckworld_api
# Inspect container logs for "Graph narrative collection startup import:"
docker compose logs shreckworld_api
```

To run it on startup, use the default configuration or explicitly enable it:
```bash
# No migration setting is required; the importer starts automatically.
docker compose up -d --build shreckworld_api
# Inspect importer summaries and any error tracebacks.
docker compose logs shreckworld_api
```

Startup imports graph narratives but does not write Source IDs onto Scene or
Milestone nodes. The Scene/Milestone reference migration is an explicit operator
action described below.

## Explicit Scene and Milestone Source-reference migration

The operator command first reruns the entity-to-Source import, then resolves
each Scene and Milestone's legacy `DERIVED_FROM` entity through its deterministic
Narrative collection entry. It writes `source_id`, `source_section_id`, and
`source_markdown_asset_id` properties only when the entry points to exactly one
existing same-ontology Source. Existing `DERIVED_FROM` relationships remain
untouched. The command is idempotent and prints a JSON audit report with Sources
created per ontology and every Scene/Milestone linked or unresolved.

Run from `shreckworld/`; the default is a read-only preview:

```bash
python scripts/migrate_graph_scene_sources.py
python scripts/migrate_graph_scene_sources.py --apply
```

If the entity-to-Source import reports errors, `--apply` reports the import
results but skips graph writes. See the [Scene and Milestone Source-reference
migration](SCENE_SOURCE_PROVENANCE_MIGRATION.md) for the report fields, matching
rules, evidence recovery, and rollback behavior.

Back up ShreckWorld's database and media before deploying this change.
Rollback is setting `SHRECKWORLD_GRAPH_IMPORT_ON_STARTUP=false` and restarting
the service; imported SQL
records/files remain until explicitly deleted or restored from backup.
