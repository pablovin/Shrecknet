# ShreckWorldAPI

Shares Shrecknet identity; querying/resolution never mutates game state.

## Methods

### `worlds(self)`

No description.

### `sources(self, world_id)`

No description.

### `query(self, world_id, question, *, library_item_id=None, selection=None, top_k=6, include_trace=False)`

No description.

### `resolve_action(self, world_id, action, *, facts=None, library_item_id=None)`

No description.

### `readiness(self)`

Check the actual local semantic encoder; unavailable models raise 503.

### `start_indexing(self, world_id, item_id)`

Start a structured semantic generation (admin/world_builder token).

### `indexing_job(self, world_id, item_id, *, job_id=None)`

Poll a specific attempt, or the latest attempt when job_id is omitted.

### `aclose(self)`

No description.
