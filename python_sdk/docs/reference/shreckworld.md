# ShreckWorldAPI

Shares Shrecknet identity; querying/resolution never mutates game state.

## Methods

### `worlds(self)`

No description.

### `sources(self, world_id)`

No description.

### `query(self, world_id, question, *, library_item_id=None, library_item_ids=None, selection=None, top_k=6, include_trace=False, mode='evidence', retrieval_strategy='single', response_detail=None)`

Ask for single-path output or opt-in planned evidence/grounded answer output.

### `start_agent_query(self, world_id, question, *, library_item_id=None, library_item_ids=None, selection=None, top_k=6, include_trace=True, mode='evidence', response_detail='concise')`

Queue a ShreckWorld planned query, optionally with a grounded answer, and return its lifecycle.

### `agent_job(self, world_id, job_id)`

Read a ShreckWorld agent job; only its owner or an admin can poll it.

### `model_settings(self)`

Read the ShreckWorld LLM target; requires admin/world_builder access.

### `update_model_settings(self, model_shreckworld_synthesizer)`

Set the shared ShreckWorld planner, reviewer, and answer model target.

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
