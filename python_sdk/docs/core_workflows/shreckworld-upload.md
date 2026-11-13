# ShreckWorld source upload

ShreckWorld uses a separate API base URL (Compose default `http://localhost:8121`)
and the existing Shrecknet bearer token. `sdk.shreckworld` provides typed source
reading, semantic questions, model readiness, and indexing-job access. Upload
examples use `httpx`, an SDK dependency, for multipart source submission.
Shrecknet's ontology/world resources do not target this service.

Source items belong directly to an existing ontology, without a collection creation
step. Integer IDs in `sdk.shreckworld.sources(7)` and `query(7, question)` use the
direct ontology routes. Source/answer models expose `ontology_id`; new sources
have null `shreckworld_id`. The upload example accepts `--ontology-id` for this
workflow and retains `--world-id` only for old collections.

The [runnable upload example](../../examples/11_shreckworld/upload_document.py)
sends multipart `auto_extract_metadata=true` and a source under the legacy `pdf`
field. It prints the saved item and downloads its cover when available:

```bash
export SHRECKNET_TOKEN='your-existing-access-token'
python python_sdk/examples/11_shreckworld/upload_document.py book.pdf --ontology-id 7
```

Use `--title 'Reviewed title'` to override extracted title metadata. Upload requires
an `admin` or `world_builder` token. The response has `title`, nullable `authors`,
nullable `description`, and nullable `cover_url`; cover requests use the same
bearer token. Autofill completes during upload and does not start indexing.

See the canonical [ShreckWorld upload API](../../../Documentation/API/SHRECKWORLD_UPLOAD_API.md)
for metadata precedence, supported formats, fallbacks, error responses, and
compatibility. Legacy uploads with a `title` query parameter remain supported.

## Track indexing

After `POST /admin/shreckworlds/{world_id}/library-items/{item_id}/index`, save the
returned job `id`. Poll `GET /admin/shreckworlds/{world_id}/library-items/{item_id}/index-jobs/{job_id}`
with the same manager token. Use `processing_seconds`, `progress`, and
`metrics.phase`/counts/warnings to display work; stop at `completed` or `failed`.
The existing `/index-status` endpoint returns the latest attempt instead.
Processing time prefers recorded monotonic phase durations; while running it
estimates time since the last checkpoint. See the
[job timing contract](../../../Documentation/API/SHRECKWORLD_INDEXING_API.md#job-response).

Starting indexing again after a failed job retries the complete pipeline, including
fresh source parsing, chunking, Book Map construction, and embeddings. No extra
SDK argument is required. Each indexing attempt bypasses parser-cache reads and
reports `metrics.parser_cache_hit=false`; parsing can take as long as the initial
attempt. A failed retry preserves any previous active generation. See the
[indexing recovery contract](../../../Documentation/API/SHRECKWORLD_INDEXING_API.md#routes-and-access).

During PDF parsing, current Docling versions update `metrics.processed_pages` and
`metrics.total_pages` on each heartbeat interval (five seconds by default).
Display these existing counters; completed pages count successful layout/table
processing, with duplicates and out-of-order page events handled. Parsing may
continue with export/normalization after all pages complete. Older Docling versions
without callbacks retain indeterminate page progress. OCR is disabled.

`metrics.parser_table_structure_enabled` records whether TableFormer reconstruction
was enabled (`null` on historical attempts). For parsing experiments, compare
`metrics.parser_seconds["conversion"]` with `parser_cache_hit=False`; changing
table reconstruction can alter chunk counts and embedding time. See the
[comparison procedure](../../../Documentation/API/SHRECKWORLD_INDEXING_API.md#comparing-table-reconstruction-costs).

Run the [polling example](../../examples/11_shreckworld/poll_indexing_job.py):

```bash
python python_sdk/examples/11_shreckworld/poll_indexing_job.py --world-id WORLD_ID --item-id ITEM_ID --job-id JOB_ID
```

Historical jobs may have `null` processing times and metrics, or retain a
historical `hash` backend. New indexing uses verified `sentence_transformers`
(PyTorch), `onnx`, or `openvino` and fails explicitly when the selected backend
is unavailable. Switching backend requires reindexing. Old hash/wrong-model
sources require reindexing for semantic retrieval. See the canonical
[indexing API](../../../Documentation/API/SHRECKWORLD_INDEXING_API.md) for strict
readiness, structural parsing, timing semantics, and deployment/rollback.

The polling example prints a terminal timing summary to stderr for both completed
and failed jobs, including `elapsed_seconds`, `queue_wait_seconds`,
`processing_seconds`, and `metrics.phase_seconds`/`metrics.parser_seconds`, sorted
by duration within each group. Parser substeps are already included in parsing.
Stdout remains JSON; `--result-file result.json` saves the terminal response for
comparisons. Check `parser_cache_hit` when comparing runs.

ONNX is the default CPU backend. Administrators can change
`shreckworld_embedding_backend` in Shrecknet's `Shreckworld` configuration tab;
the same field is available through its existing `/config/` API. Prefetch and
restart Shreckworld API/worker before reindexing with a new backend. Workers emit
`[SHRECKWORLD_INDEXING_SUMMARY]` JSON logs with the terminal timing fields for
both successful and failed attempts. See the
[configuration contract](../../../Documentation/API/SHRECKWORLD_INDEXING_API.md#portable-cpu-backends).

## Typed readiness and job access

```python
from shrecknet_client import Shrecknet

async with Shrecknet(token=token, shreckworld_base_url="http://localhost:8121") as sdk:
    readiness = await sdk.shreckworld.readiness()
    print(readiness.embedding.model, readiness.embedding.fingerprint)
    job = await sdk.shreckworld.start_indexing(world_id, item_id)
    # Poll every 1–2 seconds until status is completed/failed.
    status = await sdk.shreckworld.indexing_job(world_id, item_id, job_id=job.id)
    print(status.processing_seconds, status.metrics)
```

Starting/polling requires `admin` or `world_builder`. Omit `job_id` to fetch the
latest attempt. Models preserve phase timings, parser diagnostics, embedding
identity, and null historical values. `query(..., include_trace=True)` exposes
retrieval diagnostics, including model/reindex status and optional ranking/index
fallbacks. Citation models retain typed source-block geometry, viewer rectangles,
heading paths, complete page ranges, authenticated content URLs, and parent
`evidence_text`. Source reading and rule-review contracts remain in the
[reader workflow](shreckworld-reader.md).
