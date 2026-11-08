# ShreckWorldAPI

Shares Shrecknet identity; querying/resolution never mutates game state.

Integer scope arguments address ontology-owned items directly, e.g. `await sdk.shreckworld.sources(7)` and `await sdk.shreckworld.query(7, "Explain combat")`. Integer ontology IDs also work for action review and indexing/polling. String arguments retain legacy collection routes. `KnowledgeSource` and `KnowledgeAnswer` expose optional `ontology_id` and nullable `shreckworld_id`. Discover ontologies through the Shrecknet ontology resource; `worlds()` remains a legacy directory method; asynchronous agent-job methods also accept integer ontology IDs.

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

`metrics.embedding_backend` accepts `sentence_transformers`, `onnx`, `openvino`,
and historical `hash`. Final job responses retain per-step `phase_seconds` and
parser substep `parser_seconds`; processing/queue/elapsed durations remain
available. See the [indexing workflow](../core_workflows/shreckworld-upload.md#track-indexing)
for display and comparison guidance.

### `aclose(self)`

No description.

## Model-target search option

`LLMModelTarget` (Shrecknet) and `ShreckWorldModelTarget` (ShreckWorld) accept
`web_search_enabled: bool = False`. Saved targets preserve the option independently
of reasoning. See [shared agent web search](../../../Documentation/Architecture/LLM_WEB_SEARCH.md)
for capability discovery and supported provider behavior.
