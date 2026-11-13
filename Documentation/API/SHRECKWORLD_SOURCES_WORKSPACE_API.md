# Sources Workspace API (first implementation slice)

The ShreckWorld service owns Sources (formerly library items), reusable hierarchy templates,
editorial structure revisions, and document generation references. Shrecknet owns ontology
identity, authentication, and world authorization. ShreckOS owns the editor.

## Ownership

All workspace routes are under `/ontologies/{ontology_id}`. The caller must be
authenticated and authorized to read the referenced ontology by Shrecknet.
Mutating routes require the existing `require_world_manager` role check.

No endpoint in this slice writes Neo4j nodes, ontology definitions, scenes, milestones,
embeddings, or existing document generations. The original PDF's Book Map remains
immutable and independently retrievable.

## Contracts

- `GET /source-hierarchies`: latest version per hierarchy, including archived.
- `GET /source-hierarchies/{id}/versions/{version}`: version-pinned template.
- `POST /source-hierarchies`: create from `HierarchyInput`.
- `POST /source-hierarchies/{id}/image?expected_version=N`: upload a PNG, JPEG or WebP image (maximum 5 MB), appending a version.
- `GET /source-hierarchies/{id}/versions/{version}/image`: authenticated, version-pinned image read.
- `PUT /source-hierarchies/{id}`: append a new version, requires
  `expected_version` of the current version (409 on conflict).
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
`source_ids` attachments. The API validates strict hierarchy levels,
no cycles, sibling-order uniqueness, artwork ownership and document
references. Attachments must belong to **the same ontology and the same
Narrative/Explanatory category**. Drag/reparent operations are saved
as one revision. A stale edit returns HTTP 409 rather than overwriting
another person's changes.
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

- `GET /sources/{source_id}/document`: bounded read-only preview of
  TXT, HTML, DOCX and Markdown originals; PDF returns an indicator so the
  authorized original-byte streaming endpoint can be used for the browser
  PDF viewer.
- `PUT /sources/{source_id}/document`: revision-safe rich editing for
  **uploaded Markdown**. This writes a new immutable Markdown file version
  with a new source checksum and marks its existing index stale. Previously
  indexed generations are retained and are not passed off as current.
- Newly **authored Markdown** continues through its
  `/sources/{source_id}/organization` editing contract. This prevents
  competing editable authorities for the same file.
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
  provenance, or the Architect. The opt-in startup importer described below
  **copies** scene-derived narrative material into Sources without altering the graph.
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

## Optional graph narrative source import (startup)

Set `SHRECKWORLD_GRAPH_IMPORT_ON_STARTUP=true` to run the **insert-only,
read-only-from-graph** import after ShreckWorld initializes its own SQL schema.
The default is false. The startup task runs in a daemon thread with bounded connection retries
(up to 12 attempts at ten-second intervals); failures are logged without
blocking normal service startup. Reboot to retry an unavailable
Neo4j connection or a previously failed group. Concurrent importer processes
use a file lock under the ShreckWorld media directory.

Docker Compose mounts two inputs **read-only** for the importer:
`/legacy-shrecknet-data/shrecknet.db` (SQL ontology definitions) and
`/legacy-shrecknet-media` (original local image media). The ShreckWorld
service accesses Neo4j using its existing Shrecknet connection credentials from
`configs/neo4j.env`. Optional overrides are
`SHRECKWORLD_LEGACY_ONTOLOGY_DATABASE_PATH`,
`SHRECKWORLD_LEGACY_MEDIA_ROOT`, and
`SHRECKWORLD_GRAPH_NEO4J_URI/USER/PASSWORD/DATABASE`.
Do not mount the legacy SQLite database read-write.

The importer discovers distinct `Scene -[:DERIVED_FROM]-> EntityInstance`
anchors, then reconstructs narrative ancestors through typed
`EntityInstance -[:RELATES_TO]-> EntityInstance` relationships using the
SQL `entity_relationships` definitions. It recognizes common narrative
entity type names (Adventure, Campaign, Story Arc, Arc, Year, Story,
Session, Timeline and Chapter). Each unique ancestor tree is grouped into an
ontology-owned Narrative Source and uses a version-pinned hierarchy template.
One section is created per distinct graph entity, including ancestral
Adventure and Story Arc nodes; hierarchy/root cards retain the names,
descriptions and imported images. Scene and graph writes are **never** made.

Every section with non-empty graph `text` and/or `autogenerated_text` owns
its own immutable Markdown asset. Both texts are retained when different; the
`*_linked` graph fields are deliberately excluded as derivative renderings.
Sources also retain the existing full-tree materialized Markdown snapshot.
Section `metadata.markdown_asset_id` and `metadata.image_asset_id` are
opaque references to the new `source_assets` table. Only Shrecknet-owned
`/media/` paths mounted inside the read-only legacy media directory are
eligible for image copying; external image URLs are **not fetched**. Original
image URLs remain in `metadata.legacy_image_url` when copying is impossible.
Missing or oversized images do not prevent text import.

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
`(ontology_id, instance_id, entity_instance_id)`. Repeated startup runs skip
already imported nodes, including nodes that users have edited afterward.
New graph Stories under already imported ancestors are appended without
rewriting existing sections. Deleted or renamed nodes are not automatically
removed, and changes to already imported graph text are not synced. All
ambiguous ancestry, missing mappings or oversized organizations are reported
as errors and skipped. The import is intentionally **not** a graph migration
or a semantic-indexing job.

Example:
```bash
# Set before starting/restarting the API:
SHRECKWORLD_GRAPH_IMPORT_ON_STARTUP=true docker compose up -d --build shreckworld_api
# Inspect container logs for "Graph narrative startup import:"
docker compose logs shreckworld_api
```

Back up ShreckWorld's database and media before enabling the importer.
Rollback is disabling the setting and restarting the service; imported SQL
records/files remain until explicitly deleted or restored from backup.
