# ShreckWorld structured indexing and job polling

Each newly indexed generation includes a validated Book Map V4 built from
independent structural evidence, normalized blocks/pages, resolved sections,
and the exact chunks created for the generation. Map publication is part of the
SQL activation transaction. A failed build prevents activation and leaves the
prior searchable generation in place. The manifest records map/resolver/chunking
versions, recipe alignment, and quality counters. Job metrics include resolver
time, conflicts, coverage, uncertain/unresolved nodes, table fallbacks, and
context-reduced chunks, plus retained image/table nodes and text-bearing source
blocks. SQL and Qdrant child records carry recipe/map versions, resolved paths,
source block references, content type, and context-reduction status.

The API startup still backfills missing or old V3 maps. For existing V4
artifacts, run the dry-run restore command documented in the
[reader API](SHRECKWORLD_READER_API.md#book-map); `--write` persists a validated
artifact. This operation never parses, embeds, mutates Qdrant, or rewrites
chunks/FTS. Generations without a retained artifact are classified as requiring
a reparse.

New vectors use recipe `book-map-v4-e5-context-v1`; existing
`docling-e5-context-v2` and `pymupdf-e5-context-v2` indexes remain eligible for
active retrieval until explicitly reindexed.

New items are directly owned by ontologies. Use `/ontologies/{ontology_id}/items` and its item/index subroutes; no ShreckWorld creation is required. The `/admin/shreckworlds/.../library-items` routes below remain compatibility contracts. See [direct routes and migration](SHRECKWORLD_READER_API.md#direct-ontology-item-routes) for authorization, item ownership, deployment and rollback.

## Routes and access

All indexing routes require a Shrecknet bearer token with role `admin` or
`world_builder`. Every lookup validates world → item → job ownership.

| Operation | Endpoint |
| --- | --- |
| Start | `POST /admin/shreckworlds/{world_id}/library-items/{item_id}/index` |
| Latest status | `GET /admin/shreckworlds/{world_id}/library-items/{item_id}/index-status` |
| Specific attempt | `GET /admin/shreckworlds/{world_id}/library-items/{item_id}/index-jobs/{job_id}` |
| Model readiness | `GET /ready` (public operational metadata, no credentials) |

Legacy `/embed` and `/embedding-status` remain compatible. Start returns `202`
with the queued job. Save its `id` and poll every 1–2 seconds until `completed` or
`failed`; no websocket is required. Readiness is checked before queueing, and
again by the worker. A missing/broken semantic encoder returns `503` before a
new job is created; later parsing/inference/projection errors fail the job.
Starting an item already queued/embedding returns `409`. Missing or mismatched
world/item/job returns `404`; missing credentials return `401`, insufficient
roles `403`. Job status is separate from the last successful active generation.

## Structured indexing contract

The worker uses shared normalization and structural parent/child algorithms from
`shreckdoc`, without Librarian's Neo4j persistence. PDF parsing is selectable:
Compose defaults to experimental PyMuPDF `to_chunks()`; standalone defaults to
Docling. PDF parsing can also use the optional MinerU inference service. DOCX/
Markdown continue to use Docling, and plain text uses paragraph blocks. Parsers
attempt to preserve headings, tables, lists, reading order, and available
provenance; extraction is heuristic and cannot guarantee complete recognition.
The PyMuPDF adapter retains source element records and table/figure view fields
alongside chunk IDs. Mixed-type chunk text is marked unknown instead of being
called a paragraph; an available textless figure is retained as an empty picture
block with its available page and region provenance. Parser cache identity is versioned with each
adapter so earlier grouped outputs do not hide newly captured evidence. The
`to_chunks()` API is supplied by PyMuPDF Layout and is unavailable in ordinary
PyMuPDF installs.
MinerU source blocks retain empty text and unrecognized types as evidence; its
table/list/equation source content is kept in native metadata and captions become
separate caption blocks linked to their source block. MinerU `structured_content`
bounding boxes use the documented 0–1000 normalized coordinate mapping. Invalid
or out-of-range boxes are omitted and reported in parser diagnostics.
Docling PDF table structure uses the configured TableFormer mode (`fast` by default),
heading inference is enabled where supported, and OCR is disabled on the
Docling/PyMuPDF paths. MinerU uses its local inference service and may OCR
image-only pages according to the selected tier. DOCX/Markdown/text have logical
page 1; no PDF coordinates are invented for them.

Parents retain complete display evidence; children are embedded with contextual
`passage:` text and the real E5 tokenizer. Every extracted text block is indexed,
including page headers/footers, prose, tables, and captions; headings provide
structural context through the resolved heading path. Tables preserve their own
retrieval units and image pixels are excluded. OCR and image identification are
not performed by Docling/PyMuPDF. Structural boundaries, table headers, and tokenizer windows
prevent implicit model truncation. Every inference batch checks count, finite
values, dimension, and normalization. Source SHA-256 and PDF page counts are
verified. No LLM gateway/provider or web call performs indexing.

Successful completion means validated semantic children were acknowledged by
Qdrant and the complete generation was atomically activated in SQL/FTS. Failed
reindexing preserves the prior active generation. Historical/hash generations
remain stored but are excluded from semantic retrieval until reindexed; source
reading and covers remain available. A large image-heavy PDF may produce little
native text, and neither size nor completion establishes OCR coverage.

See [canonical architecture](../Architecture/SHRECKWORLD.md) for algorithm details,
storage, activation, retrieval, and artifact retention.

## Job response

The existing `id`, `library_item_id`, `status`, `progress`, `detail`, `created_at`,
and `completed_at` remain. All timestamps are explicit UTC. Additive fields:

| Field | Meaning |
| --- | --- |
| `started_at` | Nullable worker-start timestamp |
| `updated_at` | Nullable last committed progress timestamp |
| `elapsed_seconds` | Creation to now/completion, including queue time |
| `processing_seconds` | Start to now/completion; `null` before start or for unknown historical timing |
| `queue_wait_seconds` | Creation to start; grows while queued; `null` for historical finished jobs lacking start data |
| `metrics` | Structured diagnostics below, or `null` before worker start/for historical jobs |

When phase measurements exist, `processing_seconds` sums the worker's monotonic
`phase_seconds`, avoiding clock adjustments during parsing or inference. While
running, it extrapolates from the last `updated_at` checkpoint with nonnegative
wall-clock time. `elapsed_seconds` adds queue wait to that measured duration.
Historical jobs without measurements retain timestamp-based timing. Phase totals
exclude small gaps between phases and final commit overhead.

`status` is `queued`, `running`, `completed`, or `failed`. Example completed job
with illustrative counts/timings (not a benchmark):

```json
{
  "id": "JOB_ID",
  "library_item_id": "ITEM_ID",
  "status": "completed",
  "progress": 100,
  "detail": "Embedded 25 semantic children with 6 parents",
  "created_at": "2026-10-15T12:00:00Z",
  "started_at": "2026-10-15T12:00:01Z",
  "updated_at": "2026-10-15T12:00:18Z",
  "completed_at": "2026-10-15T12:00:18Z",
  "elapsed_seconds": 18.0,
  "processing_seconds": 17.0,
  "queue_wait_seconds": 1.0,
  "metrics": {
    "phase": "completed",
    "progress_mode": "determinate",
    "parser_name": "docling",
    "parser_version": "installed-docling-version",
    "parse_quality": {"native_pdf_pages": 10, "docling_pages": 10, "tables_detected": 2},
    "sections": 6,
    "blocks": 50,
    "parent_chunks": 6,
    "tokenizer_limit": 500,
    "embedding_fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "embedding_revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
    "processed_pages": 10,
    "total_pages": 10,
    "text_pages": 8,
    "empty_pages": 2,
    "extracted_characters": 10000,
    "processed_chunks": 25,
    "total_chunks": 25,
    "embedding_backend": "sentence_transformers",
    "embedding_model": "intfloat/multilingual-e5-small",
    "warnings": ["2 pages yielded no retrievable text; OCR is not performed."],
    "phase_seconds": {"validating_model": 0.3, "parsing": 12.0, "chunking": 1.0, "embedding": 3.5, "storing": 0.2}
  }
}
```

`metrics.phase` follows `validating_model` → `parsing` → `chunking` → `embedding`
→ `storing` → `completed`, or `failed` from any stage. `progress_mode` is
`indeterminate` while opaque model/chunk construction runs and before a parser's
first page event, then `determinate` during page processing, batch encoding,
storage, and completion. With Docling versions exposing
`convert(progress_callback=...)`, `processed_pages` counts unique successfully
completed pages, including their layout/table processing, rather than the highest
page number encountered. Out-of-order events and duplicates do not inflate it;
failed pages do not count as successfully processed. `total_pages` is known from
the native PDF where available. Polling frontends can render the existing
`metrics.processed_pages / metrics.total_pages` fields without an API/model change.

Docling page events are marshalled from the parser thread to the owning job thread and
committed at the next `parser_heartbeat_seconds` interval (default five seconds),
with a final flush when conversion returns or fails. Parsing progress moves from
5% through at most 34%; chunk construction starts at 35%. Finishing all pages
does not finish parsing: document assembly, canonical export, normalization, and
cache publication may remain. Older Docling versions without the public callback
retain indeterminate parsing and report page counts after normalization. PyMuPDF
and MinerU do not report per-page progress through this adapter: parsing remains
indeterminate until conversion finishes. Their job response still exposes the
overall `progress` percentage and `metrics.phase`; frontends can render that
existing percentage consistently for all parser choices. A heartbeat continues during remote
task polling and canonical exports. Child counts become known after chunk
construction. Docling uses CPU PDF rendering,
layout detection, and fast table reconstruction when enabled.

`text_pages` counts pages with retrievable text, excluding headers/footers and
heading-only pages; `empty_pages` is the remaining page count. `parse_quality`
reports observed native/parser page counts (`docling_pages`, `pymupdf_pages`, or `mineru_pages`), pages without structured blocks,
detected/empty tables and lists, pictures, and explicit diagnostic count, without
guessing unrecognized structures. It may also include parser metadata such as
`mineru_version` as a string (for example, `"4.1.7"`); consumers should preserve
quality values as typed JSON values rather than assuming every value is a count.
`sections`, `blocks`, and `parent_chunks` count
normalized/stored structures. `processed_chunks`/`total_chunks` count embedded
children, not parents. `extracted_characters` counts normalized block text.
Warnings include parser diagnostics and pages without retrievable native text.
Docling/TableFormer warning logs are also captured, including explicit dropped
PDF-cell reports that are not part of `ConversionResult.errors`. Quality adds
`parser_log_records`, `table_cell_drop_reports`, `reported_dropped_table_cells`,
and `reported_table_cells`. These count observed reports, which may overlap;
they are not a guessed unique-cell coverage percentage or failed-table count.
Up to 32 distinct warning/error messages of at most 1,000 source characters are
retained. Such warnings identify potential parsing loss for reader review;
successful E5 inference does not certify perfect table recognition.

New jobs report `sentence_transformers` (PyTorch), `onnx`, or `openvino` according
to the configured embedding backend. Historical metrics may contain `hash`;
this is preserved as historical evidence, never used as a semantic fallback.
Model/fingerprint/revision/tokenizer fields remain `null` until verified. Phase
seconds use a monotonic clock. Progress is an approximate stage indicator:
validation 1%, parsing 5–34% (5% without callbacks), chunking 35%, encoding 50–90%, storage 95%, success
100%. Failure retains last progress. Batches checkpoint at phase boundaries and
at most once per second; a single long inference/remote write may delay updates.
Timers continue growing on polling; progress is not an estimated completion time
or a guarantee of worker liveness.

## Readiness and configuration

`GET /ready` returns `status=ready`, the effective `parser` and `pdf_parser`
(`docling`, `pymupdf`, or `mineru`), `configured_pdf_parser`, optional
`parser_fallback_reason`, configured `parser_device`, table-structure
toggle and TableFormer mode, `parser_artifacts_present=true`, and `embedding` containing `ready`, `backend`,
`model`, actual `fingerprint`, resolved snapshot `revision`, `dimension`, and
`device`. A local model directory uses its directory name as revision label and
still receives a content fingerprint. Readiness requires real inference with
finite unit vectors and a relevant-query/passage probe, not merely a successful
import. The default E5 snapshot is pinned; API and worker must share the same
actual weight/tokenizer fingerprint. Runtime inference errors invalidate cached
readiness until a new probe passes. A missing encoder/artifact directory yields
`503`. PyMuPDF selection also requires the installed `to_chunks` API. MinerU
selection checks the configured service's `/v1/tiers` at process startup. If it
is unavailable, ShreckWorld uses Docling for that process and logs a warning;
the persisted admin setting remains MinerU. `/ready` then reports
`pdf_parser=docling`, `configured_pdf_parser=mineru`, and a non-empty
`parser_fallback_reason`. Restart ShreckWorld after MinerU becomes available to
resume MinerU parsing. Docling artifacts remain required for DOCX/Markdown and
rollback. `GET /health`
remains liveness only; Compose uses `/ready` for health.

| `SHRECKWORLD_` setting | Default / operation |
| --- | --- |
| `EMBEDDING_MODEL_ID` | `intfloat/multilingual-e5-small` |
| `EMBEDDING_REVISION` | `614241f622f53c4eeff9890bdc4f31cfecc418b3`; change deliberately with model ID |
| `EMBEDDING_DIMENSION` | `384`; must match actual model and Qdrant collection |
| `EMBEDDING_DEVICE` | `cpu`; default comes from the Shrecknet admin runtime config; a non-empty `SHRECKWORLD_EMBEDDING_DEVICE` overrides it. CUDA requires the `torch` backend and a GPU-enabled deployment |
| `EMBEDDING_BACKEND` | `onnx`; accepts `onnx`, `torch`, `openvino`. Default comes from the Shrecknet admin runtime config; a non-empty environment value overrides it. ONNX/OpenVINO require CPU |
| `RUNTIME_CONFIG_FILE` | Shared non-secret Shrecknet projection; Compose `/service-config/shreckworld.json`, local repository `shrecknet/databases/service-config/shreckworld.json` |
| `EMBEDDING_ARTIFACTS_PATH` | `./models/embedding`; Compose `/hf-cache/shreckworld-embedding`; converted model cache, writable only during prefetch |
| `EMBEDDING_LOCAL_FILES_ONLY` | `true`; runtime uses prefetched local snapshots |
| `EMBEDDING_BATCH_SIZE` | `16`, range 1–128 |
| `EMBEDDING_THREADS` | `4`, range 1–32 |
| `DOCLING_ARTIFACTS_PATH` | `./models/docling`; Compose `/hf-cache/docling` |
| `PDF_PARSER` | `docling`; configurable in Shrecknet admin. A non-empty environment value overrides it. `pymupdf` requires Python 3.11+ and PyMuPDF 2.1 |
| `MINERU_API_URL` | `http://mineru:8000`; internal MinerU V1 API base URL, used only when `PDF_PARSER=mineru` |
| `MINERU_API_KEY` | Empty; optional Bearer key for a separately protected MinerU service |
| `MINERU_TIER` | `standard`; MinerU PDF inference tier |
| `MINERU_VERSION` | `4.1.7`; expected service version included in parser-cache identity |
| `MINERU_TIMEOUT_SECONDS` | `1800`, range 60–7200; deadline for remote MinerU parsing and result download |
| `MINERU_POLL_INTERVAL_SECONDS` | `2`, range 0.25–30; interval between task status requests |
| `PARSER_DEVICE` | `auto`; independently configured from `EMBEDDING_DEVICE`. `auto` selects CUDA when available in the container and CPU otherwise. Shrecknet admin also offers explicit `cpu` and `cuda`; explicit CUDA requires a GPU-enabled image |
| `PARSER_HEARTBEAT_SECONDS` | `5`, range 0.1–60 |
| `PARSER_TABLE_STRUCTURE_ENABLED` | `true` enables Docling table reconstruction; `false` skips reconstruction while retaining layout analysis. Configurable in Shrecknet admin; does not configure MinerU's table processing |
| `PARSER_TABLE_FORMER_MODE` | `fast` by default; `accurate` requests the more expensive TableFormer mode. Configurable in Shrecknet admin |
| `RERANKER_MODEL_ID` | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| `RERANKER_ENABLED` | `true`; failed optional reranking retains RRF order and trace warning |
| `QDRANT_COLLECTION` | `shreckworld_semantic_v2` |

Compose prefetches models with `shreckworld_model_prefetch`; it checks the
persistent Hugging Face volume locally first and contacts the model source only
when the requested snapshot is missing. API/worker mount the cache read-only and
disable runtime downloads. Prefetch does not recursively change ownership of
cached model files; the default downloaded permissions allow the service user
to read them. Both images require Docling and
Sentence Transformers; the old `[semantic]` extra remains accepted but is no
longer needed. The worker wrapper preflights E5 and parser artifacts before
starting a single-task threaded Celery worker, avoiding PyTorch prefork issues.
The separate `mineru` service is opt-in through Compose profile `mineru`; it is
an NVIDIA GPU service and is not started by the default stack. Build/start it through the GPU-aware launcher with
`./scripts/compose_up.py --build -d --mineru` before selecting
MinerU in administration. Its pinned MinerU 4.1.7 image downloads the Standard
model bundle during the first build; configure `MINERU_MODEL_SOURCE=modelScope`
when the host should use ModelScope instead of Hugging Face. No MinerU port is
published on the host; ShreckWorld calls it over the private Compose network.
When MinerU is selected, ShreckWorld checks its `/v1/tiers` endpoint at process
startup. If MinerU is unavailable, that process uses Docling and logs a warning;
the persisted admin configuration remains MinerU. `/ready` reports
`pdf_parser=docling`, `configured_pdf_parser=mineru`, and a non-empty
`parser_fallback_reason`. This startup decision is cached for the process, so
restart ShreckWorld after MinerU becomes available to resume MinerU parsing. If
MinerU is available, `/ready` reports `pdf_parser=mineru` and its tier details.
Docling artifacts are still required in either case.
The GPU Compose overlay builds CUDA-enabled PyTorch and exposes the GPU; the
selected devices come from the Shrecknet runtime settings. The repository's
`scripts/compose_up.py` probes Docker GPU access and selects the GPU overlay or
CPU build. With parser device `auto`, Docling uses CUDA when the selected image
and container expose it, and CPU otherwise. Explicit CUDA does not silently fall
back. The parser device and table mode are included in cache identity, so changing
either triggers a fresh parse.
Semantic readiness does not guarantee perfect document
recognition or factual correctness, and future hardware/model errors can still
occur; such errors must be visible instead of generating hash vectors.

### Comparing PDF parsers

Compose selects `SHRECKWORLD_PDF_PARSER=pymupdf` unless the root `.env`
overrides it. Rebuild and recreate the API and worker after updating:

```bash
docker compose build shreckworld_api
docker compose up -d shreckworld_api shreckworld_worker
```

Drain indexing jobs before switching configuration. Upload the same PDF as two
separately titled items, index one with `pymupdf`, switch the setting to
`docling` and recreate both services, then index the other. Compare representative
queries, citations and source regions as well as `parser_name`, `parser_version`,
`parser_seconds`, `phase_seconds`, chunk counts, warnings and `parser_cache_hit`
in the job responses or `[SHRECKWORLD_INDEXING_SUMMARY]` logs. Use cache misses
for comparable cold parsing times. The parser cache is isolated by parser and
version. All paths use the same encoder and hard tokenizer/provenance validation;
PyMuPDF layout chunks serve as child units and may be split further. Figure IDs
and bounding boxes are retained, but image-only content is not OCRed or embedded
as images on Docling/PyMuPDF. MinerU can OCR image-only pages and retains
detected heading levels, but its output is still heuristic. Tables, layout, and
hierarchy can differ or be missed; no parser is guaranteed to be more accurate.

For MinerU, start the GPU service first, select `mineru` in the same admin group,
restart ShreckWorld API/worker, and verify `/ready` reports `pdf_parser=mineru`
with a populated `mineru` status. Index the same PDF as a separate source item
for each parser so each active generation remains available for side-by-side
Book Map JSON comparison. MinerU's `doc_title`/`paragraph_title` levels become
the normalized section hierarchy used by the existing deterministic Book Map
builder; this does not add an LLM-generated or semantically validated outline.
Compare title/parent/page-anchor precision against a manually reviewed set of
books before deciding which parser is better for the collection. Use the same
embedding settings and compare cold runs (`parser_cache_hit=false`), map JSON,
page coverage, warnings, timing, and retrieval citations. Cache identity includes
MinerU service URL, configured service version, and tier.

Reindexing the *same* item replaces its active generation only on success; it
does not retain two simultaneously searchable versions. Separate items allow
side-by-side queries. No database migration is needed. To roll back, set
`SHRECKWORLD_PDF_PARSER=docling`, recreate API/worker, and reindex the affected
PDFs; a failed reindex leaves the prior active generation searchable.

### Comparing table reconstruction costs

Set `SHRECKWORLD_PARSER_TABLE_STRUCTURE_ENABLED=false` in the root `.env` and
recreate Shreckworld API/worker, then start a new indexing attempt for the same
source. Layout analysis and its existing settings remain enabled, OCR stays off,
and TableFormer reconstruction is skipped. ShreckWorld normally uses fast mode;
the shared adapter retains enabled, accurate-mode defaults for Librarian callers.
Fast mode may produce different table evidence from accurate mode. Disabling
reconstruction may lose table rows/cells and
structured table evidence; review retrieval quality before retaining its index.

The flag and table mode participate in parser-cache identity: switching either forces a fresh parse
without deleting the previous cache. Subsequent identical runs can hit the new
cache, so verify `metrics.parser_cache_hit=false` when comparing timings.
Existing accurate-mode cache entries are not reused for fast-mode parsing.
`metrics.parser_table_structure_enabled` records the choice in API/SDK job metrics
and `[SHRECKWORLD_INDEXING_SUMMARY]` logs; historical jobs have `null`.
Compare `metrics.parser_seconds.conversion` to isolate the conversion-path change,
and `metrics.phase_seconds.parsing` for the complete parsing cost. Changed table
content can also change chunk counts and embedding time; the embedding phase is
not a direct measure of TableFormer cost. Keep thread counts and other parser
settings fixed for comparisons.

Restore the flag to `true` and recreate API/worker to re-enable fast-mode reconstruction.
Reindex the experimental source to restore table-aware evidence. Old caches and
source files are preserved, and no database migration is required. Parsing options
do not change encoder identity; the newly activated document replaces the prior
retrievable generation only after successful indexing as usual.

### Portable CPU backends

ONNX Runtime is the default E5 embedding backend. PyTorch and OpenVINO are
selectable alternatives, applied consistently to indexing and query encoding. They do not change
Docling parsing or the optional cross-encoder reranker. ONNX is the first choice
for hosts with different CPU vendors. OpenVINO is primarily optimized for Intel
hardware; verify its supported CPU/OS requirements on each host. Neither backend
guarantees a speedup for a particular document or machine.

The Shrecknet administrator configuration frontend renders the `ShreckWorld
Indexing` group from `GET /config/schema`. It provides dropdowns for PDF parser
(Docling, PyMuPDF, MinerU),
parser device, TableFormer mode, embedding backend and device, plus a switch for
table extraction. Save selections through the existing administrator-only
`PUT /config/` route:

```json
{"shreckworld_pdf_parser":"mineru","shreckworld_parser_device":"cuda","shreckworld_parser_table_structure_enabled":true,"shreckworld_parser_table_former_mode":"accurate","shreckworld_embedding_backend":"onnx","shreckworld_embedding_device":"cpu"}
```

Defaults are Docling, CPU parser, table reconstruction enabled, fast TableFormer,
ONNX and CPU embeddings. ONNX/OpenVINO with CUDA embeddings are rejected; the
admin form disables that incompatible device choice and switches back to CPU
when selecting a CPU-only backend. Invalid values return `400`; reads, schema,
and saves retain existing administrator authorization. All these settings have
`change_impact=service_restart`; restart the ShreckWorld API and worker after
saving. Re-index sources after parser changes; prefetch model artifacts before
switching to a backend/device that needs new artifacts. Shrecknet's configuration
database remains authoritative. Loading/reloading or saving settings atomically
publishes all six non-secret values into
`shrecknet/databases/service-config/shreckworld.json`. ShreckWorld reads this
projection without accessing Shrecknet's databases. Do not edit the generated
file; use the frontend/API or the initial seed `configs/shrecknet.initial.json`.
A missing projection uses the defaults above; an invalid projection fails
configuration explicitly. A publication error fails the configuration operation.

For each setting, a non-empty `SHRECKWORLD_*` environment variable overrides the
shared file, which overrides the service default. Compose leaves these managed
values empty by default so admin selections apply. Remove explicit deployment
overrides when moving control to the frontend.

On first deployment, rebuild Shrecknet and all Shreckworld images to install the
shared configuration module and runtime dependencies. Compose's Shreckworld
image includes all three backends; later selections need no rebuild. Drain/stop
indexing before changing the selection, run
`docker compose run --rm shreckworld_model_prefetch`, then restart/recreate
Shreckworld API and worker together and reindex sources. The cached encoder stays
active until its process restarts; the saved selection is not an indication that
an existing process has switched. Verify `/ready` after restart. The projection
directory is mounted read-only into API, worker, and prefetch. Initial directory
ownership is prepared by `shrecknet_permissions` before prefetch runs.

Local installations include ONNX in the base package. Install
`python -m pip install -e './shreckworld[openvino]'` to enable OpenVINO locally,
and run `python -m app.prefetch` from `shreckworld/` after changing the selection.
Dependency ranges retain the Optimum 1.x interface used
by Sentence Transformers 3.x, with PyTorch 2.6–2.8 for exporter compatibility.
Use a dedicated local environment for these extras; choose matching CPU
PyTorch/torchvision wheels before installation, as the Compose image does.

Prefetch exports the pinned local source snapshot into a separate directory
keyed by source fingerprint and backend, publishing it only after export succeeds.
Runtime loads these artifacts with export/downloads disabled, allowing the cache
to remain read-only. Missing artifacts fail readiness with `503`; there is no
automatic backend fallback. Failed export leaves no published backend directory.
To replace a damaged export, stop API/worker, remove only that backend's directory
under `EMBEDDING_ARTIFACTS_PATH`, and rerun prefetch.

ONNX uses `CPUExecutionProvider`, runtime graph optimization, and the configured
thread count. OpenVINO uses CPU compilation with FP32 precision and the configured
thread count. Exports are unquantized: no AVX512/VNNI-specific quantization or
host-compiled executable is shipped between machines. Each runtime optimizes for
the CPU on which it loads. Install runtime wheels supported by each host's OS and
architecture; portability of model files does not make a runtime wheel universal.

Converted encoder identity includes the backend, source fingerprint, and all
exported artifact bytes, including pooling/tokenizer configuration and external
weights. API and worker must load the same export. Switching backend or changing
exported artifacts requires reindexing: old generations remain stored but are
excluded from retrieval until compatible generations are activated. Copy the
same portable export when sharing an index between hosts; independent exports
may have different fingerprints. This change needs no database migration.
Rollback selects `torch`, restarts the services, and uses the original
source snapshot. Sources last indexed with the converted backend need reindexing
for PyTorch retrieval. Keep original sources and caches until rollout is accepted.

Benchmark representative books on each machine using `metrics.phase_seconds`.
`metrics.parser_seconds` breaks parsing into available substeps such as
`conversion`, `canonical_export`, `primitive_extraction`, and `cache_lookup`.
These substeps are included in `parsing`, so do not add them to the phase total.
Compare parser cache hits and misses separately using `metrics.parser_cache_hit`.
The terminal polling example prints total/queue/processing time and both timing
groups to stderr, sorting each group by duration; stdout retains job JSON.
Failed jobs show the measured work up to failure; historical jobs may lack timings.

The worker also emits one `[SHRECKWORLD_INDEXING_SUMMARY]` JSON console log per
terminal attempt: `job_id`, `library_item_id`, `status`, `embedding_backend`,
`elapsed_seconds`, `queue_wait_seconds`, `processing_seconds`, `phase_seconds`,
`parser_seconds`, `parser_cache_hit`, `parser_table_structure_enabled`,
`processed_chunks`, and `total_chunks`.
Successful attempts log at INFO (enabled by the default worker); failures log at
WARNING and retain partial timings. These are the persisted job measurements;
parser substeps overlap parsing. Post-activation artifact/retired-vector cleanup
is outside the recorded indexing phases. Summary logs omit document contents,
vectors, credentials, and error text. Capture worker stdout/stderr with the normal
container logging driver to retain these summaries beyond container lifetime.

See [Sentence Transformers backend guidance](https://www.sbert.net/docs/sentence_transformer/usage/efficiency.html)
and [OpenVINO supported devices](https://docs.openvino.ai/nightly/documentation/compatibility-and-support/supported-devices.html).

## Frontend and SDK polling

```javascript
const path = `/admin/shreckworlds/${worldId}/library-items/${itemId}/index-jobs/${jobId}`;
const response = await fetch(`${shreckworldApi}${path}`, {
  headers: { Authorization: `Bearer ${token}` }
});
if (!response.ok) throw new Error(await response.text());
const job = await response.json();
// Show processing_seconds, metrics.phase, progress_mode, counts and warnings.
// Poll every 1–2 seconds until completed/failed; stop on component unmount.
```

Use `sdk.shreckworld.start_indexing(world_id, item_id)` and
`sdk.shreckworld.indexing_job(world_id, item_id, job_id=job.id)` for typed Python
access. `sdk.shreckworld.readiness()` returns verified model identity. See the
[Python workflow](../../python_sdk/docs/core_workflows/shreckworld-upload.md) and
[runnable polling example](../../python_sdk/examples/11_shreckworld/poll_indexing_job.py).

## Migration, deployment, and rollback

1. Back up the current SQLite database consistently and retain original source
   files before upgrading. Do not deploy while old workers are indexing.
2. Build `shreckworld_api` with Compose (including the named `shreckdoc`
   context). It builds the shared `shreckworld:local` image used by model
   prefetch, API, and worker, avoiding duplicate service image builds. Run model
   prefetch to successful completion. A network/model-download failure is a
   deployment error, not a reason to enable hash fallback.
3. Run the updated `create_schema()` before workers consume jobs. It adds new
   structured-document tables, nullable chunk provenance/context fields, job
   timing/metrics columns, SQLite FTS5, and its cleanup trigger idempotently.
   Existing source/job rows and legacy vectors are preserved; no model identity
   or geometry is fabricated for old data.
4. Recreate both API and worker. Verify `/ready`, matching API/worker fingerprints,
   and registered `shreckworld.embed.library_item` before indexing.
5. Reindex existing sources to build structured semantic generations. Until then,
   legacy/hash or wrong-fingerprint sources are excluded from retrieval and
   reported in `trace.sources_requiring_reindex`. Original reading works normally.

The new collection separates semantic generations from the legacy hash index.
A source's first successful structured activation replaces its old SQL chunks;
subsequent attempts preserve the prior active generation until success. Remote
projection/SQL activation cannot form one distributed transaction: staged or
retired points may survive cleanup failures, but active SQL document IDs and
fingerprints prevent their retrieval. Failed/retired artifacts consume disk until
explicit retention cleanup. Source/world deletion removes SQL/FTS/media and
attempts remote cleanup; old legacy collections remain an operational rollback
artifact rather than active retrieval storage.

Rollback requires restoring the pre-upgrade SQLite backup and matching old images
if old hash retrieval is needed. Do not compare a hash query encoder with new E5
rows or assume equal vector dimensions imply compatibility. Keep the old Qdrant
collection/source files until rollback is no longer required. New nullable columns
alone need not be dropped. The Librarian graph schema and runtime are unchanged;
its shared-package extraction preserves its existing behavior.
