# OntologyInstancesAPI

Ontology instance CRUD, search, and summary endpoints.

## Methods

### `create(self, payload)`

Create ontology instance.

### `list(self)`

List ontology instances with optional filters.

### `get(self, instance_id)`

Fetch ontology instance by id.

### `update(self, instance_id, payload)`

Update ontology instance.

### `delete(self, instance_id)`

Delete ontology instance.

### `count(self)`

Count ontology instances for filters.

### `search(self)`

Search instances by query within ontology scope.

### `basic(self)`

Return summary page for instance listing UIs.

### `resolve_entities(self)`

Resolve entity instance ids into scoped ontology entities.

### `scene_counts(self, instance_ids)`

Return scene counts for the provided ontology instance ids.
