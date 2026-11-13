# ShreckWorld structured knowledge service

## Purpose and ownership

ShreckWorld is a separately deployed world-knowledge service inside the Shrecknet
frontend. It owns ontology-associated source items, original files, structured document generations,
embeddings, retrieval jobs and citations. Shrecknet ontologies provide the authoritative
world identity for both Atlas graph content and ShreckWorld source items. The existing
Shrecknet `World` remains an ontology-grouping concept. Separate `ShreckWorld` rows
are retained only for legacy compatibility; new items need no such parent.
It also owns a persisted `model_shreckworld_synthesizer` provider/model target,
administered through its settings API and shared by ShreckWorld LLM stages.

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

The service lives in `shreckworld/` and Compose exposes host port `8121` mapped to
container port `8120`. In-network callers must target the container port, so the
Compose default for `SHRECKNET_SHRECKWORLD_BASE_URL` is
`http://shreckworld_api:8120`; use `http://localhost:8121` only for host-side or
native (non-Compose) access. Shrecknet is the sole identity provider. ShreckWorld validates the
forwarded bearer token against `/auth/jwks`, including signature, issuer,
audience, expiry, subject, and role. Every authenticated user can discover worlds,
read original sources, query evidence, and request conservative rule review.
There is currently no per-world membership ACL. Only `admin` and `world_builder`
can manage sources, start/poll indexing jobs, and update service model settings.

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
| ShreckWorld agent | `POST /shreckworlds/{world_id}/agent-jobs`, `GET .../{job_id}` |
| Shared ShreckWorld LLM target | `GET` / `PUT /admin/settings/model-targets/shreckworld_synthesizer` |
| Liveness/model readiness | `GET /health`, `GET /ready` |

Legacy `/embed` and `/embedding-status` routes remain supported. Upload accepts
PDF, DOCX, Markdown, and text under the legacy `pdf` multipart field. Optional
`auto_extract_metadata` autofills missing details and renders the first PDF page
as a private cover. See [upload API](../API/SHRECKWORLD_UPLOAD_API.md) and
[reader API](../API/SHRECKWORLD_READER_API.md) for their complete contracts.

## Ruleset registry and ShreckTables (Phase 1)

ShreckWorld persists ruleset drafts, ontology bindings, immutable published
definition snapshots, generation setup runs, and ShreckTables in additive SQLite
tables. Shrecknet remains authoritative for ontology, user, and Character Agent
identity; ShreckWorld stores stable references. Each table pins one existing
integer `ontology_id` and one published version ID. A table is a live-game record,
not a second ontology or a legacy `shreckworlds` row.

The `/api/v1/game-engine` routes require `admin` or `world_builder`; ontology-scoped
operations resolve access with the caller's Shrecknet bearer token. Review and
publication require an administrator. Table updates use revision preconditions,
and archive preserves the record and participant links while blocking further
changes. Participant associations validate the user and, when present, verify a
Character Agent's ontology and creator before storing its ID.

Generation runs are persisted and dispatched through Celery, but Phase 1 performs
no extraction. A successful run explicitly reports `extraction_performed=false`
and zero candidates. See the [Phase 1 API contract](../API/SHRECKWORLD_GAME_ENGINE_API.md)
for payloads, errors, roles, source revision checks, and compatibility.

## Structured indexing pipeline

Book Map V3 remains the current persisted and API representation. The proposed
V4 structural resolver is not active in parsing, indexing, retrieval, or reads.
The initial typed V4 envelope is defined in `app/schemas/book_map.py`; shared,
storage-independent extraction evidence records live in
`shreckdoc/shreckdoc/structure_types.py`. These types preserve optional source,
table, and page-accounting evidence while existing normalized record constructors
retain their defaults. Defining a schema does not enable V4 persistence or routes.
Synthetic evidence inputs for its implementation tests live in
`shreckworld/tests/fixtures/bookmap_v4/`; they cover outline destinations,
printed contents, heading candidates, and retained table/image/caption/unknown
blocks. Their expected outcomes preserve evidence and uncertainty only; they do
not assert final parentage or parser coverage. The V4 implementation plan and
its migration sequence are maintained in the coordinating frontend repository.

1. Verify the real sentence-transformer model and source SHA-256 before parsing.
2. Parse PDFs with the configured Docling or PyMuPDF layout adapter; Compose
   defaults to the experimental PyMuPDF adapter. DOCX/Markdown use Docling;
   plain text uses paragraph blocks with logical page 1. OCR is disabled.
   The PyMuPDF adapter consumes `to_chunks()` with isolated tables, records
   section paths, chunk/element/table/figure IDs and page boxes, and retains
   extraction diagnostics. Layout and heading recognition are heuristic.
3. Save parser-specific JSON, optional Docling Markdown, and a normalized JSON artifact.
   Normalize pages (physical number and printed label), nested sections, blocks,
   reading order, source references, bounding boxes, and character spans.
4. Use Librarian's shared builder to create complete semantic parents and
   structural children. Headers/footers are excluded from embeddings. Table row
   groups repeat headers; lists split at items; prose splits at sentence boundaries
   and, when necessary, explicit tokenizer windows with overlap. PyMuPDF's
   chunk boundaries are retained as child units unless further splitting is
   needed for the complete contextual E5 input.
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
retrievable child, and incomplete conversions/page-count mismatches fail
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
- `shreckworld_agent_jobs`: owner-scoped planned-query request, lifecycle stage,
  result, and safe failure detail. SQLite startup creates this table for existing
  service installations; deleting a world cascades its agent-job records.
- `shreckworld_settings`: service-owned model target. Schema initialization
  creates the table automatically; the singleton target is shared across worlds.

Original files and private covers remain under
`media/shreckworlds/{world_id}/{item_id}/`. Each attempt stores
`parsed/{job_id}/docling_document.json` or `pymupdf_document.json`,
`normalized_document.json`, and `ingestion_manifest.json`. Docling Markdown is
optional; plain text stores `source_document.json`. Writes use atomic rename.
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
`library_item_id` remains available for compatibility; `library_item_ids` scopes
retrieval to multiple sources. Omitting both searches every eligible source in
the requested ShreckWorld. SQL activation and world membership constrain every
retrieval branch, including Qdrant fallback. `source_coverage` distinguishes
requested sources, indexed sources actually searched, sources without a ready
index, and sources requiring a compatible reindex.

If Qdrant is temporarily unavailable, retrieval can rank the same verified E5
vectors from canonical SQL and reports `qdrant_fallback=true`. It never generates
hash query vectors. Citations keep matched child display text and bounded parent
or sibling-window evidence, heading paths, physical pages/printed labels, source
hash, block-level geometry, and authenticated content links. The API also returns
safe top-left percentage rectangles for the existing reader. Bounding boxes
identify source blocks; they do not certify exact word-level quote alignment.
Unknown coordinate origins/dimensions produce no invented rectangles. See the
[reader contract](../API/SHRECKWORLD_READER_API.md) for viewer behavior and errors.

The query endpoint defaults to evidence mode and returns extractive evidence for
existing clients. Answer mode sends a bounded package of complete retrieved
passages to ShreckLLM and receives structured claims, missing information, and
source conflicts. ShreckWorld checks the response schema and requires every claim
and conflict to cite an ID supplied to the model; the API rejects malformed
outputs and invalid IDs. This citation-membership check does not independently
prove semantic entailment. Retrieval relevance and model readiness do not prove
rule applicability or factual correctness. Rule review remains conservative and
does not mutate game state. Web-search policy is persisted but no web provider is
called.

Synthesis uses the separately configured ShreckLLM endpoint, provider, and model.
It does not require LLM availability for evidence mode or original-source reading.
The evidence budget is configurable and estimated at four characters per token;
complete passages are included or omitted as units, never truncated for synthesis.
Timeouts, malformed model output, and oversized evidence are surfaced as explicit
HTTP errors. Answer mode reports `partial` and describes coverage gaps when
requested sources or retrieved passages are omitted. Optional query trace reports
retrieval, synthesis, and total request timings for workload-specific
measurement; these metrics are not latency guarantees. See the
[reader API contract](../API/SHRECKWORLD_READER_API.md) for request/response
fields, configuration, and error behavior.

The opt-in `retrieval_strategy="planned"` path is owned by the ShreckWorld agent,
not Elder or Librarian. Simple queries use a deterministic one-need plan;
complex queries may use one structured ShreckLLM planning call that sees the
question and authorized source metadata but no retrieved chunks or source IDs.
The service resolves authorized active generations once, searches each need
sequentially through the shared E5/Qdrant, FTS5, exact, RRF, and reranker stack,
then retains source-distinct logical parent evidence under a complete-unit budget.
Sequential searches intentionally reuse the request's synchronous SQLAlchemy
session rather than sharing it across threads. A batched optional coverage review
must cite exact quotes present in returned evidence; failures remain unverified.
One targeted follow-up round is bounded to missing/partial/unresolved needs.
Planned mode never synthesizes an answer; `mode="answer"` is rejected for this
strategy until a later milestone integrates the existing grounded-answer path.

Asynchronous clients can submit the same planned-evidence operation as a
ShreckWorld-owned agent job. The job records its authenticated owner and is
visible only to that owner or an admin. The `shreckworld_agent` Celery queue
shares the single-task worker with indexing, preserving the existing memory bound.
Job results contain the same planned evidence contract as the synchronous
opt-in path. No agent runtime or persistence owned by Elder or Librarian is used.

## Model readiness and operation

`SHRECKWORLD_PARSER_TABLE_STRUCTURE_ENABLED` defaults to `true` and controls
Docling's `do_table_structure` through the shared adapter. Turning it off skips
TableFormer reconstruction while retaining layout analysis and the no-OCR rule.
When enabled, ShreckWorld uses TableFormer fast mode; the cache identity includes
both the enabled state and mode, so accurate-mode entries are not reused.
Terminal job metrics/logs record the enabled state. The shared adapter retains
enabled, accurate-mode defaults for existing Librarian callers.
See the [table reconstruction comparison](../API/SHRECKWORLD_INDEXING_API.md#comparing-table-reconstruction-costs)
for fresh-parse timing comparisons, evidence-quality impact, and rollback.

The shared `DoclingAdapter` accepts an optional `on_page_progress(done, total)`
callback, using Docling's public conversion progress events when supported.
Shreckworld queues events from the conversion thread and updates SQL job progress
on its owning thread during regular heartbeats. Unique successful page completions
populate the existing frontend page counters and parsing-stage progress; document
assembly/export may continue after all pages finish. Older Docling versions keep
the existing indeterminate behavior. This does not enable OCR or change extraction
algorithms; see the [page progress contract](../API/SHRECKWORLD_INDEXING_API.md#job-response).

Sentence Transformers and Docling are required image dependencies. The pinned
E5 snapshot is prefetched alongside the optional cross-encoder and Docling
layout/table artifacts. Runtime uses local caches with remote downloads disabled.
The cache can be shared with Shrecknet without sharing its runtime/storage.

`app.services.embedding` owns ONNX Runtime (default), PyTorch, and OpenVINO loading
and prefetch export. The Shrecknet `/config/schema` group `shreckworld` exposes
restart-required parser and embedding settings: PDF parser, parser device,
table extraction toggle/mode, embedding backend and embedding device. Shrecknet
publishes the non-secret JSON projection through `shreckconfig.shreckworld`;
ShreckWorld mounts it read-only and applies it when constructing runtime
settings. Parser device `auto` resolves against CUDA availability at service
startup; runtime diagnostics and parser cache identity use the effective CPU or
CUDA device. Its configuration database remains independent. A non-empty
`SHRECKWORLD_*` deployment value overrides the corresponding projected setting;
otherwise the shared file and then service defaults apply. Converted artifacts are separate from the source snapshot.
Prefetch publishes portable unquantized artifacts, while read-only serving
processes apply CPU optimizations locally. Converted artifact bytes and backend
participate in encoder identity; switching backend requires reindexing and matching
API/worker exports. Existing PyTorch fingerprints remain unchanged. Terminal jobs
log `[SHRECKWORLD_INDEXING_SUMMARY]` with persisted timings and counts. See the
[CPU backend contract](../API/SHRECKWORLD_INDEXING_API.md#portable-cpu-backends)
for installation, portability limits, measurements, and rollback.

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

The Celery app explicitly includes `app.tasks.embedding` and `app.tasks.agent`,
registering `shreckworld.embed.library_item` on `shreckworld_embedding` and
`shreckworld.agent.query` on `shreckworld_agent` without API imports.
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

## Direct ontology ownership and legacy transition

`ShreckWorldLibraryItem.ontology_id` directly references the authoritative Shrecknet ontology. New items have `shreckworld_id = NULL`. A library is only the item view for an ontology; ontology creation and item listing never provision an intermediary container. The direct API verifies the ontology with Shrecknet using the caller's bearer credential. It uses the trusted origin/prefix already configured by `SHRECKWORLD_SHRECKNET_JWKS_URL`. Unavailable owner access fails closed; an empty item array is returned only after successful authorization.

`app.repositories.source_scope` centralizes direct and legacy item predicates. `OntologyScope` is transient, authorized request metadata consumed by the existing source/retrieval pipeline, not a stored entity or an ontology-to-collection resolver. Its internal string representation does not identify a ShreckWorld record. Source transport is shared with compatibility routes. Original assets, source mutation, indexing and query selection check item/scope membership. Planned synchronous retrieval and delegated Companion evidence support direct ontology scope. Persisted asynchronous agent jobs also store `ontology_id` directly; their worker reconstructs the transient scope without a collection.

SQL resolves active, encoder-compatible documents from directly owned items. Qdrant searches the SQL-authorized document IDs and fingerprint; citation hydration also checks the scoped SQL rows. This preserves existing vectors when source ownership changes without relaxing ontology isolation. New vector payloads carry `ontology_id`. Ingestion reads direct ownership without requiring a collection row; source IDs, revisions and file paths remain canonical.

Startup adds the nullable ownership column/index and relaxes the legacy item-parent constraint. Existing SQLite tables are rebuilt transactionally with foreign keys temporarily disabled on that connection, copying all source columns and preserving indexes, triggers and child references; foreign-key verification precedes commit. PostgreSQL-style installations use `ALTER COLUMN ... DROP NOT NULL`. Startup does not guess ownership for old sources. The manager-only preview/apply contract in the [reader API](../API/SHRECKWORLD_READER_API.md#legacy-item-migration) performs verified migration separately.

SQLite migration records existing `PRAGMA foreign_key_check` violations under
the rebuild's write lock, preserves rowids as well as public IDs, and rolls back
if the rebuild introduces any new violations. Historical orphan rows do not block
this schema upgrade: they are retained without inventing missing owners or
deleting job history, and a warning logs their count. This migration does not
repair historical integrity issues. Before rollout, take a consistent SQLite
backup and retain source files; investigate preserved orphan records separately.
If startup reports new violations, retain the database and logs for diagnosis;
the rebuild transaction is rolled back and foreign-key enforcement is restored.

Legacy parent references remain aliases for existing links, local reading-progress namespaces and recovery. Legacy world deletion refuses migrated items, preventing cascading data loss. No automatic ontology-deletion purge is introduced: direct operations reject missing/inaccessible ontologies and retained sources require administrative recovery. Snapshot owner data and files together before rollout. Cross-installation restores require verifying ontology identity before applying numeric mappings; an ID match alone cannot prove identity. Full rollback uses coordinated snapshots; older frontends do not discover newly uploaded direct items.

## Readiness diagnostics for evidence consumers

The retrieval scope owner in `app/services/query.py` classifies requested books
by usable active generation, not solely the latest indexing-job status. A queued
or failed replacement does not invalidate searchable compatible evidence.
`source_coverage` counts searched, incompatible/reindex and remaining not-ready
sources without overlap. `sources_requiring_attention` identifies scoped source
IDs/titles with `index_not_ready`, `incompatible_index` or `empty_index` reasons.
Both single and planned query responses expose these details; navigation and
citation provenance remain unchanged. Companion consumes partial readiness as a
coverage limitation, retaining passages for compact semantic review and safe
extractive recovery. See the [reader contract](../API/SHRECKWORLD_READER_API.md).
