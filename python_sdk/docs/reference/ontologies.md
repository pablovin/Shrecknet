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
