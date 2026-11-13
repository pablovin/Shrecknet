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

## Current boundaries

- Authored Markdown is persisted as real files but is **not yet** indexed
  into full-text or semantic retrieval. Narrative source indexing is a separate
  integration step; until then it is not queryable by Elder/ShreckWorld.
- Hierarchy template images are stored in protected media with version-pinned authenticated reads and an administrator-only upload endpoint. Image uploads append a template version.
- This slice does **not** migrate existing Story/Adventure graph nodes, revise
  scene provenance, or modify the Architect.
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
