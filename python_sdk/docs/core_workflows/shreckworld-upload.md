# ShreckWorld source upload

ShreckWorld uses a separate API base URL (Compose default `http://localhost:8121`)
and the existing Shrecknet bearer token. `sdk.shreckworld` provides typed source
reading, semantic questions, model readiness, and indexing-job access. Upload
examples use `httpx`, an SDK dependency, for multipart source submission.
Shrecknet's ontology/world resources do not target this service.

The [runnable upload example](../../examples/11_shreckworld/upload_document.py)
sends multipart `auto_extract_metadata=true` and a source under the legacy `pdf`
field. It prints the saved item and downloads its cover when available:

```bash
export SHRECKNET_TOKEN='your-existing-access-token'
python python_sdk/examples/11_shreckworld/upload_document.py book.pdf --world-id WORLD_ID
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

Run the [polling example](../../examples/11_shreckworld/poll_indexing_job.py):

```bash
python python_sdk/examples/11_shreckworld/poll_indexing_job.py --world-id WORLD_ID --item-id ITEM_ID --job-id JOB_ID
```

Historical jobs may have `null` processing times and metrics, or retain a
historical `hash` backend. New indexing uses verified `sentence_transformers`
only and fails explicitly when the model is unavailable. Old hash/wrong-model
sources require reindexing for semantic retrieval. See the canonical
[indexing API](../../../Documentation/API/SHRECKWORLD_INDEXING_API.md) for strict
readiness, structural parsing, timing semantics, and deployment/rollback.

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
