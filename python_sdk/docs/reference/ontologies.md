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
- `scenes(ontology_id, *, query=None, related_entity_id=None, source_entity_id=None, has_perspectives=False, skip=0, limit=30) -> AtlasScenePage`: scene name/description search before pagination, with exact-source or broader related-entity filters. Each row distinguishes its owning instance from its nullable provenance `source_page`. Counts exclude private perspectives except for admins.
- `scene_source(ontology_id, scene_id) -> AtlasSourceReference`: canonical provenance page for an evidence link; no fallback to the scene's owning container.
- `elders(ontology_id, *, skip=0, limit=100) -> list[AtlasElderCard]`: active world-assigned Elder portraits in newest-first service order. Fetch the next page only when the returned page is full.

All four operations require authenticated world graph access. See the [backend scene discovery contract](../../../Documentation/API/SCENE_GRAPH_API.md#atlas-discovery) for authorization, ordering, empty/error behavior, and rollout requirements. These models are available from `shrecknet_client.atlas`.
