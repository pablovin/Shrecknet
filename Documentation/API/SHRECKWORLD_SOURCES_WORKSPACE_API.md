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
- `POST /sources`: create an authored Source with required `title`,
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

## Current boundaries

- The new organization API persists authored text but does **not yet** index it
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
