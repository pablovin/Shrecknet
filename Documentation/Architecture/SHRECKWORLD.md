# ShreckWorld structured knowledge service

## Purpose and ownership

ShreckWorld is a separately deployed world-knowledge service inside the Shrecknet
frontend. It owns its worlds, original sources, structured document generations,
embeddings, retrieval jobs, and citations. The existing Shrecknet `World` remains
an ontology-grouping concept; a `ShreckWorld` has a separate persistence boundary.

Librarian and ShreckWorld share **algorithms**, through the local `shreckdoc/`
package. `shreckdoc.pipeline` owns the Docling adapter, normalized page/section/block
records, contextual E5 text, and tokenizer-bounded parent/child chunking.
`shreckdoc.retrieval` owns named-term extraction, reciprocal rank fusion (RRF),
and diversity selection. Librarian preserves its old import surface and graph
orchestration. ShreckWorld imports no Librarian service, Neo4j driver, graph
storage, or Librarian job. Both images install the same source package through
Compose's `shreckdoc` additional build context; local installs must install
`shreckdoc` before the applications.

## Identity and API

The service lives in `shreckworld/` and Compose exposes port `8121` (container
`8120`). Shrecknet is the sole identity provider. ShreckWorld validates the
forwarded bearer token against `/auth/jwks`, including signature, issuer,
audience, expiry, subject, and role. Every authenticated user can discover worlds,
read original sources, query evidence, and request conservative rule review.
There is currently no per-world membership ACL. Only `admin` and `world_builder`
can manage sources or start/poll indexing jobs.

| Purpose | Endpoint |
| --- | --- |
| Discover worlds/sources | `GET /shreckworlds`, `GET /shreckworlds/{world_id}`, `GET /shreckworlds/{world_id}/library-items` |
| Read one source | `GET /shreckworlds/{world_id}/library-items/{item_id}` |
| Original file/cover | `GET` / `HEAD .../{item_id}/content`, `GET .../{item_id}/cover` |
| Manage worlds | `POST` / `GET /admin/shreckworlds`, `GET` / `PATCH` / `DELETE /admin/shreckworlds/{world_id}` |
| Upload/list sources | `POST` / `GET /admin/shreckworlds/{world_id}/library-items` |
| Edit/delete source | `PATCH` / `DELETE /admin/shreckworlds/{world_id}/library-items/{item_id}` |
| Start/latest indexing status | `POST .../{item_id}/index`, `GET .../{item_id}/index-status` |
| Poll a specific attempt | `GET .../{item_id}/index-jobs/{job_id}` |
| Query/review | `POST /shreckworlds/{world_id}/query`, `POST .../{world_id}/resolve-action` |
| Liveness/model readiness | `GET /health`, `GET /ready` |

Legacy `/embed` and `/embedding-status` routes remain supported. Upload accepts
PDF, DOCX, Markdown, and text under the legacy `pdf` multipart field. Optional
`auto_extract_metadata` autofills missing details and renders the first PDF page
as a private cover. See [upload API](../API/SHRECKWORLD_UPLOAD_API.md) and
[reader API](../API/SHRECKWORLD_READER_API.md) for their complete contracts.

## Structured indexing pipeline

1. Verify the real sentence-transformer model and source SHA-256 before parsing.
2. Parse PDF/DOCX/Markdown with Docling. PDF parsing enables layout, reading order,
   accurate tables, parsed-page retention, and heading inference. OCR, remote
   services, external plugins, and exported page/picture images are disabled.
   Plain text uses paragraph blocks with logical page 1.
3. Save canonical Docling JSON, inspection Markdown, and a normalized JSON artifact.
   Normalize pages (physical number and printed label), nested sections, blocks,
   reading order, source references, bounding boxes, and character spans.
4. Use Librarian's shared builder to create complete semantic parents and
   structural children. Headers/footers are excluded from embeddings. Table row
   groups repeat headers; lists split at items; prose splits at sentence boundaries
   and, when necessary, explicit tokenizer windows with overlap.
5. Retain original `text`/display evidence separately from contextual
   `embedding_text`. The latter includes `passage:`, book title, world context,
   heading path, entry, and content type. Count the complete input with the real
   tokenizer, normally targeting 400 tokens and never exceeding the smaller of
   500 and the actual model/tokenizer limits. Inference never silently truncates.
6. Encode children in bounded batches. Verify output count, configured dimension,
   finite values, and unit norm. Parents are stored for expansion but not embedded.
7. Stage child vectors in versioned Qdrant points with world, item, document, and
   model fingerprint payloads. Verify collection size/distance and exact staged
   point count before activation.
8. In one SQL transaction, replace the item's chunk set and FTS rows, persist the
   structured generation, and switch its active document. Failure rolls back that
   SQL replacement. A prior successful generation stays active and searchable
   during a reindex and after a failed attempt.
9. Retire old remote points after commit. Inactive/orphan points cannot become
   citations because retrieval independently validates SQL activation and scope.

The active generation is independent of the latest job state. A failed reindex
sets `index_status=failed` and its error, but leaves `embedding_status=ready` when
an earlier structured generation exists. The failed job is explicit; the earlier
source evidence remains available. The document must contain at least one valid
retrievable child, and incomplete Docling conversions/page-count mismatches fail
rather than activating a partial generation. Empty/image-only pages are counted
and warned about; no OCR coverage is claimed. Explicit Docling/TableFormer
warning logs, including dropped native table-cell reports, are retained in job
warnings and quality counts. Counts describe reported events (possibly overlapping),
not an invented unique-cell recognition rate. Review affected table regions in
the original reader; verified semantic inference cannot restore text the parser
failed to recognize.

## Persistence and artifacts

SQLite remains canonical, including a mirror of semantic vectors:

- `shreckworld_documents`: source hash, parser/version, embedding version/model,
  weight/tokenizer fingerprint, dimension, manifest path, and activation state.
- `shreckworld_document_pages`: physical number, printed label, width, and height.
- `shreckworld_document_sections`: hierarchy and complete heading paths.
- `shreckworld_document_blocks`: display text, type, reading order, source reference,
  page/bounding-box/character-span provenance, and metadata.
- `shreckworld_document_chunks`: parents and children, parent/section/block IDs,
  display/contextual text, page ranges, token counts, and child vectors.
- `shreckworld_chunk_fts`: SQLite FTS5 contextual index for child title/headings/text.
- `shreckworld_embedding_jobs`: lifecycle, timestamps, and structured metrics.

Original files and private covers remain under
`media/shreckworlds/{world_id}/{item_id}/`. Each attempt stores
`parsed/{job_id}/docling_document.json`, `document.md`,
`normalized_document.json`, and `ingestion_manifest.json`; plain text stores
`source_document.json` instead of Docling artifacts. Writes use atomic rename.
Failed/retired attempt artifacts and historical structured records remain for
diagnosis, while active chunks/FTS rows are replaced. Monitor disk use across
repeated reindexing; no automatic historical-artifact retention limit is applied.
Deleting an item/world removes its files and SQL-derived records, including FTS
rows through a cleanup trigger. Qdrant cleanup is best effort after SQL deletion;
failed cleanup is logged and remaining points stay excluded by SQL scope.

The default semantic collection is `shreckworld_semantic_v2`; the prior
`shreckworld_chunks_v1` hash collection is left intact for rollback. The Qdrant
1.12 client is aligned with Compose's 1.12 server. Qdrant is internal-only and
rebuildable. Do not expose it directly or treat its payloads as authorization.

## Retrieval and citation provenance

Retrieval considers only active SQL generations whose actual model fingerprint
matches the encoder used for the query. Legacy/hash vectors and incompatible
model generations require reindexing, even when their dimensions match.

Queries combine scoped Qdrant vector search, contextual SQLite FTS5, and exact
quoted/named terms. The shared Librarian RRF merges rank positions rather than
adding incomparable raw scores. A local cross-encoder refines up to 25 candidates;
its optional failure preserves deterministic RRF order and is visible in trace.
The shared diversity selector prefers distinct semantic parents/sources. Query
`library_item_id` filtering, selected-passage revision validation, and read-only
rule review remain supported through this same retrieval entry point.

If Qdrant is temporarily unavailable, retrieval can rank the same verified E5
vectors from canonical SQL and reports `qdrant_fallback=true`. It never generates
hash query vectors. Citations keep matched child display text and bounded parent
or sibling-window evidence, heading paths, physical pages/printed labels, source
hash, block-level geometry, and authenticated content links. The API also returns
safe top-left percentage rectangles for the existing reader. Bounding boxes
identify source blocks; they do not certify exact word-level quote alignment.
Unknown coordinate origins/dimensions produce no invented rectangles. See the
[reader contract](../API/SHRECKWORLD_READER_API.md) for viewer behavior and errors.

The endpoint currently returns extractive evidence, not an LLM-generated answer.
Retrieval relevance and model readiness do not prove rule applicability or factual
correctness. Rule review remains conservative and does not mutate game state.
Web-search policy is persisted but no web provider is called.

## Model readiness and operation

Sentence Transformers and Docling are required image dependencies. The pinned
E5 snapshot is prefetched alongside the optional cross-encoder and Docling
layout/table artifacts. Runtime uses local caches with remote downloads disabled.
The cache can be shared with Shrecknet without sharing its runtime/storage.

Readiness checks actual inference: correct model dimension, finite unit vectors,
query/passage prefix behavior, and a relevant-passage ranking probe. Identity is
computed from actual model weights, tokenizer, and configuration bytes; both API
and worker must use the same fingerprint. CUDA requests fail clearly if CUDA is
unavailable. There is **no hash fallback** for indexing or queries. A runtime
inference error fails the job/request and invalidates cached readiness until a
fresh probe passes. These checks prevent false success; they do not promise that
models or hardware can never fail or that every retrieved answer is correct.

`GET /health` is liveness only. `GET /ready` returns verified embedding identity
and parser-artifact presence, or `503`. Compose health checks use `/ready`.
The API logs failed semantic warmup but keeps original-file reading available.
`python -m app.worker` verifies the encoder and artifact directory before starting
Celery. Its single-task thread pool avoids forking a warmed PyTorch model and
bounds memory; parser/model code uses its own configured CPU threads.

The Celery app explicitly includes `app.tasks.embedding`, registering
`shreckworld.embed.library_item` on `shreckworld_embedding` without API imports.
For a confirmed discarded unregistered message, rebuild the corrected worker,
verify `celery ... inspect registered`, and requeue the original matching item/job
pair only when both remain queued and no active/reserved/scheduled copy exists:

```bash
docker compose exec -T shreckworld_api celery -A app.celery_app:celery_app call shreckworld.embed.library_item --queue=shreckworld_embedding --args='["ITEM_ID", "JOB_ID"]'
```

See [indexing API](../API/SHRECKWORLD_INDEXING_API.md) for progress/timing fields,
configuration, model prefetch, migration, and rollback. See
[Librarian embedding](../Agents/Librarian/Embedding/Embedding.md) for the shared
algorithm's original graph consumer.
