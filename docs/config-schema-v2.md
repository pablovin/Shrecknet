# Config Schema v2 Contract

## Frontend Instructions
1. Call `GET /config/schema` and render `groups`.
2. Each entry in `groups` includes a `property`:
   - `runtime`
   - `restart_required`
3. Use `field_meta` per field (`type`, `help`, `secret`, `multiline`, `nullable`, `change_impact`).
4. Hide or disable fields where `frontend_editable=false`.
5. Badge fields by `change_impact`: `hot`, `service_restart`, `locked`.
6. Save only editable changed fields to `PUT /config`.

## Groups Returned by `GET /config/schema`

### App Runtime
- `id`: `app_runtime`
- `property`: `runtime`
- `fields`: `app_name`, `debug`, `event_publisher_mode`, `event_webhook_url`

### Media & Uploads
- `id`: `media_uploads`
- `property`: `runtime`
- `fields`: `media_root`, `media_base_url`, `media_public_url`, `max_image_upload_bytes`, `image_max_width`, `image_max_height`, `max_pdf_upload_bytes`, `library_max_pdf_bytes`

### Background Workers
- `id`: `background_workers`
- `property`: `runtime`
- `fields`: `celery_task_always_eager`, `celery_expires_architect_seconds`, `celery_expires_novelist_seconds`, `celery_expires_reconciliation_seconds`, `celery_stale_reaper_enabled`, `celery_stale_reaper_interval_seconds`, `celery_stale_reaper_max_task_age_seconds`

### AI Agents
- `id`: `ai_agents`
- `property`: `runtime`
- `fields`: `enable_ai_agents`, `shreckllm_base_url`

### Architect Agent
- `id`: `architect`
- `property`: `runtime`
- `fields`: `model_architect_scene_chunking`, `model_architect_entity_proposal`, `model_architect_milestone_proposal`, `model_architect_entity_generation`, `model_agents_repair_json`

### Elder Agent
- `id`: `elder`
- `property`: `runtime`
- `fields`: `model_elder_planner`, `model_elder_synthesis`, `model_elder_character_incorporation`, `elder_embedding_inference_concurrency`, `elder_query_embedding_timeout_s`, `elder_embedding_warmup_on_worker_start`, `elder_embedding_manager_enabled`, `elder_embedding_queue_max_size`, `elder_embedding_batch_max_size`, `elder_embedding_batch_wait_ms`, `elder_embedding_cache_size`, `elder_embedding_request_timeout_s`, `embedding_runtime_enabled`, `embedding_runtime_queue_max_size`, `embedding_runtime_batch_max_size`, `embedding_runtime_batch_wait_ms`, `embedding_runtime_cache_size`, `embedding_runtime_request_timeout_s`, `embedding_runtime_startup_timeout_s`, `embedding_runtime_fail_open_health`, `embedding_model_id`, `embedding_dimension`, `embedding_device`, `embedding_chunk_size`, `embedding_chunk_overlap`

### Novelist Agent
- `id`: `novelist`
- `property`: `runtime`
- `fields`: `model_novelist_planning`, `model_novelist_prose`, `model_novelist_critic`

### Librarian Agent
- `id`: `librarian`
- `property`: `runtime`
- `fields`: `model_librarian_planner`, `model_librarian_synthesis`, `model_librarian_character_incorporation`

### Character Agent
- `id`: `character_agent`
- `property`: `runtime`
- `fields`: `model_character_agent_framing`, `model_character_agent_deliberation`, `model_character_agent_character_incorporation`, `model_character_agent_scene_interpretation`, `model_character_agent_update`, `character_agent_embodiment_evidence_tokens`, `character_agent_embodiment_max_aspects`, `character_agent_embodiment_max_goals`

### Security Tokens
- `id`: `security_tokens`
- `property`: `restart_required`
- `fields`: `jwt_issuer`, `jwt_audience`, `jwt_kid`, `jwt_access_token_expiry_minutes`

### Legacy Migration
- `id`: `legacy_migration`
- `property`: `runtime`
- `fields`: `old_database_url`

## Notes
- Env-only bootstrap fields are intentionally hidden from this schema endpoint.
