# OntologiesAPI

Ontology CRUD and world stats endpoints.

## Methods

### `create(self)`

Create ontology.

### `list(self)`

List ontologies with optional filters and pagination.

### `get(self, ontology_id)`

Fetch ontology by id.

### `update(self, ontology_id, **fields)`

Patch ontology fields.

### `delete(self, ontology_id)`

Delete ontology by id.

### `world_stats(self)`

Return world stats for one or multiple ontologies.

### Atlas discovery

- `scene_sources(ontology_id, *, skip=0, limit=10) -> AtlasSourcePage`: distinct provenance pages with titles, artwork, scene counts, and `has_more`. Increment `skip` by the returned result count. Limit is 1–100.
- `folio_detail(ontology_id, page_id) -> AtlasFolioDetail`: bounded Living Folio page content. `page_id` is the ontology-instance page ID, not an entity-instance ID. The `folio_projection` marker reports that scene bodies and relationship collections were omitted; use the dedicated reads below.
- `folio_authors(ontology_id, page_id) -> list[AtlasAuthor]`: distinct public author summaries for a page.
- `folio_relationships(ontology_id, page_id, *, entity_id=None, query=None, group=None, skip=0, limit=30) -> AtlasRelationshipPage`: bounded relationship rows and group counts, with direction and separate entity/page target IDs. Limit is 1–100.
- `scenes(ontology_id, *, query=None, related_entity_id=None, source_entity_id=None, has_perspectives=False, related_only=False, skip=0, limit=30) -> AtlasScenePage`: scene name/description search before pagination, with exact-source or related-entity filters. `related_only=True` requires `related_entity_id` and excludes provenance edges. Each row distinguishes its owning instance from its nullable provenance `source_page`. Counts exclude private perspectives except for admins.
- `scene_source(ontology_id, scene_id) -> AtlasSourceReference`: canonical provenance page for an evidence link; no fallback to the scene's owning container.
- `elders(ontology_id, *, skip=0, limit=100) -> list[AtlasElderCard]`: active world-assigned Elder portraits in newest-first service order. Fetch the next page only when the returned page is full.

All operations require authenticated world graph access. See the [backend Living Folio contract](../../../Documentation/API/SCENE_GRAPH_API.md#living-folio-page-reads) for page projections, authorization, empty/error behavior, and rollout requirements. These models are available from `shrecknet_client.atlas`.

Example: [read a Living Folio page](../../examples/02_ontology_management/03_read_living_folio.py).
