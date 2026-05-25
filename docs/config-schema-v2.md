# Config Schema v2 Contract

## Frontend Instructions
1. Call `GET /config/schema` and render `groups_v2`.
2. Use `field_meta` per field (`type`, `help`, `secret`, `multiline`, `nullable`).
3. Hide/disable `frontend_editable=false`.
4. Badge by `change_impact`: `hot`, `service_restart`, `locked`.
5. Save only editable changed fields to `PUT /config`.

## Runtime Changeable (`hot`)
- `app_name`
- `architect_milestone_extraction_concurrency`
- `architect_scene_entity_extraction_concurrency`
- `celery_expires_architect_seconds`
- `celery_expires_novelist_seconds`
- `celery_expires_reconciliation_seconds`
- `celery_stale_reaper_enabled`
- `celery_stale_reaper_interval_seconds`
- `celery_stale_reaper_max_task_age_seconds`
- `celery_task_always_eager`
- `debug`
- `default_top_k`
- `elder_embedding_batch_max_size`
- `elder_embedding_batch_wait_ms`
- `elder_embedding_cache_size`
- `elder_embedding_inference_concurrency`
- `elder_embedding_manager_enabled`
- `elder_embedding_queue_max_size`
- `elder_embedding_request_timeout_s`
- `elder_embedding_warmup_on_worker_start`
- `elder_query_embedding_timeout_s`
- `embedding_chunk_overlap`
- `embedding_chunk_size`
- `embedding_device`
- `embedding_dimension`
- `embedding_model_id`
- `embedding_runtime_batch_max_size`
- `embedding_runtime_batch_wait_ms`
- `embedding_runtime_cache_size`
- `embedding_runtime_enabled`
- `embedding_runtime_fail_open_health`
- `embedding_runtime_queue_max_size`
- `embedding_runtime_request_timeout_s`
- `embedding_runtime_startup_timeout_s`
- `enable_ai_agents`
- `event_publisher_mode`
- `event_webhook_url`
- `image_max_height`
- `image_max_width`
- `library_max_pdf_bytes`
- `max_image_upload_bytes`
- `max_pdf_upload_bytes`
- `media_base_url`
- `media_public_url`
- `media_root`
- `model_architect`
- `model_architect_scene_chunking`
- `model_elder`
- `model_librarian`
- `model_novelist`
- `model_novelist_draft`
- `novelist_scene_pipeline_batch_size`
- `novelist_scene_pipeline_max_concurrency`
- `neo4j_database`
- `neo4j_password`
- `neo4j_uri`
- `neo4j_user`
- `novelist_elder_query_concurrency`
- `novelist_elder_query_timeout_s`
- `novelist_scene_pipeline_batch_size`
- `novelist_scene_pipeline_max_concurrency`
- `old_database_url`
- `openai_api_key`
- `shreckllm_max_retries`
- `shreckllm_request_timeout_s`

## Requires Restart (`service_restart`)
- `jwt_access_token_expiry_minutes`
- `jwt_audience`
- `jwt_issuer`
- `jwt_kid`
- `shreckllm_base_url`

## Frontend Locked (`locked`)
- `celery_broker_url`
- `celery_result_backend`
- `cors_allow_credentials`
- `cors_allow_headers`
- `cors_allow_methods`
- `cors_allow_origin_regex`
- `cors_allow_origins`
- `cors_max_age`
- `database_url`
- `jobs_database_url`
- `jwt_private_key_pem`
- `jwt_public_key_pem`

## Field Metadata (all settings fields)

| Field | Type | Nullable | Secret | Multiline | Requires Restart | Frontend Editable | Change Impact | Help |
|---|---|---:|---:|---:|---:|---:|---|---|
| `app_name` | string | no | no | no | no | yes | hot | Application name shown in logs and diagnostics. |
| `debug` | boolean | no | no | no | no | yes | hot | Enable debug behavior and verbose internals. |
| `cors_allow_origins` | string_list | no | no | yes | no | no | locked | Allowed CORS origins. |
| `cors_allow_origin_regex` | string | no | no | no | no | no | locked | Regex for allowed dynamic origins. |
| `cors_allow_credentials` | boolean | no | no | no | no | no | locked | Allow CORS credentials. |
| `cors_allow_methods` | string_list | no | no | yes | no | no | locked | Allowed CORS methods. |
| `cors_allow_headers` | string_list | no | no | yes | no | no | locked | Allowed CORS headers. |
| `cors_max_age` | integer | no | no | no | no | no | locked | CORS preflight cache duration in seconds. |
| `database_url` | string | no | no | no | yes | no | locked | Primary application database URL. |
| `jobs_database_url` | string | no | no | no | yes | no | locked | Background jobs database URL. |
| `media_root` | string | no | no | no | no | yes | hot | Filesystem path for stored media. |
| `media_base_url` | string | no | no | no | no | yes | hot | Base URL path used to serve media. |
| `media_public_url` | string | yes | no | no | no | yes | hot | Public absolute URL for media. |
| `max_image_upload_bytes` | integer | no | no | no | no | yes | hot | Maximum image upload size in bytes. |
| `image_max_width` | integer | no | no | no | no | yes | hot | Maximum image width in pixels. |
| `image_max_height` | integer | no | no | no | no | yes | hot | Maximum image height in pixels. |
| `max_pdf_upload_bytes` | integer | no | no | no | no | yes | hot | Maximum PDF upload size in bytes. |
| `library_max_pdf_bytes` | integer | no | no | no | no | yes | hot | Maximum total PDF size per library in bytes. |
| `old_database_url` | string | no | no | no | no | yes | hot | Legacy database URL for migration tooling. |
| `neo4j_uri` | string | no | no | no | no | yes | hot | Neo4j connection URI. |
| `neo4j_user` | string | no | no | no | no | yes | hot | Neo4j username. |
| `neo4j_password` | string | no | yes | no | no | yes | hot | Neo4j password. |
| `neo4j_database` | string | no | no | no | no | yes | hot | Neo4j database name. |
| `celery_broker_url` | string | no | no | no | no | no | locked | Celery broker URL. |
| `celery_result_backend` | string | no | no | no | no | no | locked | Celery result backend URL. |
| `celery_task_always_eager` | boolean | no | no | no | no | yes | hot | Run Celery tasks synchronously in-process. |
| `celery_expires_architect_seconds` | integer | no | no | no | no | yes | hot | Architect task expiry in seconds. |
| `celery_expires_novelist_seconds` | integer | no | no | no | no | yes | hot | Novelist task expiry in seconds. |
| `celery_expires_reconciliation_seconds` | integer | no | no | no | no | yes | hot | Reconciliation task expiry in seconds. |
| `celery_stale_reaper_enabled` | boolean | no | no | no | no | yes | hot | Enable stale-task reaper. |
| `celery_stale_reaper_interval_seconds` | integer | no | no | no | no | yes | hot | Reaper interval in seconds. |
| `celery_stale_reaper_max_task_age_seconds` | integer | no | no | no | no | yes | hot | Task age threshold for reaper in seconds. |
| `shreckllm_base_url` | string | no | no | no | no | yes | service_restart | Base URL for shreckLLM service. |
| `shreckllm_request_timeout_s` | number | no | no | no | no | yes | hot | Request timeout when calling shreckLLM. |
| `shreckllm_max_retries` | integer | no | no | no | no | yes | hot | Retry attempts for shreckLLM calls. |
| `architect_scene_entity_extraction_concurrency` | integer | no | no | no | no | yes | hot | Architect scene extraction parallelism. |
| `architect_milestone_extraction_concurrency` | integer | no | no | no | no | yes | hot | Architect milestone extraction parallelism. |
| `enable_ai_agents` | boolean | no | no | no | no | yes | hot | Global toggle for AI agent features. |
| `openai_api_key` | (implicit) | no | no | no | no | yes | hot |  |
| `model_architect_scene_chunking` | llm_target | no | no | no | no | yes | hot | Provider/model target for scene chunking. |
| `model_architect` | llm_target | no | no | no | no | yes | hot | Provider/model target for architect extraction. |
| `model_elder` | llm_target | no | no | no | no | yes | hot | Provider/model target for elder responses. |
| `model_novelist` | llm_target | no | no | no | no | yes | hot | Provider/model target for novelist. |
| `model_novelist_draft` | llm_target | no | no | no | no | yes | hot | Provider/model target for novelist draft mode. |
| `novelist_scene_pipeline_batch_size` | integer | no | no | no | no | yes | hot | Novelist per-run scene batch size for steps 2-5 fanout. |
| `novelist_scene_pipeline_max_concurrency` | integer | no | no | no | no | yes | hot | Novelist max parallel scene pipelines per run. |
| `model_librarian` | llm_target | no | no | no | no | yes | hot | Provider/model target for librarian. |
| `default_top_k` | integer | no | no | no | no | yes | hot | Default retrieval top-k. |
| `embedding_model_id` | string | no | no | no | no | yes | hot | Embedding model identifier. |
| `embedding_dimension` | integer | no | no | no | no | yes | hot | Embedding vector dimension. |
| `embedding_device` | string | no | no | no | no | yes | hot | Embedding execution device. |
| `elder_embedding_inference_concurrency` | integer | no | no | no | no | yes | hot | Embedding inference concurrency. |
| `elder_query_embedding_timeout_s` | number | no | no | no | no | yes | hot | Elder query embedding timeout. |
| `elder_embedding_warmup_on_worker_start` | boolean | no | no | no | no | yes | hot | Warm embedding stack on worker startup. |
| `elder_embedding_manager_enabled` | boolean | no | no | no | no | yes | hot | Enable embedding manager. |
| `elder_embedding_queue_max_size` | integer | no | no | no | no | yes | hot | Embedding queue maximum size. |
| `elder_embedding_batch_max_size` | integer | no | no | no | no | yes | hot | Embedding batch maximum size. |
| `elder_embedding_batch_wait_ms` | integer | no | no | no | no | yes | hot | Embedding batch wait time in ms. |
| `elder_embedding_cache_size` | integer | no | no | no | no | yes | hot | Embedding cache size. |
| `elder_embedding_request_timeout_s` | number | no | no | no | no | yes | hot | Embedding request timeout. |
| `embedding_runtime_enabled` | boolean | no | no | no | no | yes | hot | Enable embedding runtime. |
| `embedding_runtime_queue_max_size` | integer | no | no | no | no | yes | hot | Embedding runtime queue max size. |
| `embedding_runtime_batch_max_size` | integer | no | no | no | no | yes | hot | Embedding runtime batch max size. |
| `embedding_runtime_batch_wait_ms` | integer | no | no | no | no | yes | hot | Embedding runtime batch wait ms. |
| `embedding_runtime_cache_size` | integer | no | no | no | no | yes | hot | Embedding runtime cache size. |
| `embedding_runtime_request_timeout_s` | number | no | no | no | no | yes | hot | Embedding runtime request timeout. |
| `embedding_runtime_startup_timeout_s` | number | no | no | no | no | yes | hot | Embedding runtime startup timeout. |
| `embedding_runtime_fail_open_health` | boolean | no | no | no | no | yes | hot | Health checks fail-open behavior. |
| `embedding_chunk_size` | integer | no | no | no | no | yes | hot | Chunk size for embedding pipeline. |
| `embedding_chunk_overlap` | integer | no | no | no | no | yes | hot | Chunk overlap for embedding pipeline. |
| `novelist_elder_query_concurrency` | integer | no | no | no | no | yes | hot | Novelist elder query concurrency. |
| `novelist_elder_query_timeout_s` | integer | no | no | no | no | yes | hot | Novelist elder query timeout. |
| `event_publisher_mode` | string | no | no | no | no | yes | hot | Event publishing mode (e.g. logging/webhook). |
| `event_webhook_url` | string | yes | no | no | no | yes | hot | Webhook URL when webhook mode is used. |
| `jwt_issuer` | string | no | no | no | no | yes | service_restart | JWT issuer claim. |
| `jwt_audience` | string | no | no | no | no | yes | service_restart | JWT audience claim. |
| `jwt_kid` | string | no | no | no | no | yes | service_restart | JWT key identifier. |
| `jwt_access_token_expiry_minutes` | integer | no | no | no | no | yes | service_restart | Access token lifetime in minutes. |
| `jwt_private_key_pem` | string | no | yes | yes | no | no | locked | Private key PEM. |
| `jwt_public_key_pem` | string | no | no | yes | no | no | locked | Public key PEM. |
