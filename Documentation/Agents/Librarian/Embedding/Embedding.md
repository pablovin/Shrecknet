# Librarian Embedding

Librarian uses a local Docling pipeline to ingest PDF books as structured,
versioned document graphs. The PDF is never flattened to page text before
chunking.

This is the active embedding source for Librarian Query v2. Its E5 child
vectors provide precise matches, while the parent/child graph reconstructs the
complete display evidence used by final synthesis.

## Entry Points

The existing frontend and API contracts are unchanged:

- `POST /libraries/{ontology_id}/items/{item_id}/trigger-embedding`
- `GET /libraries/{ontology_id}/items/{item_id}/embedding/export`
- `POST /libraries/{ontology_id}/items/{item_id}/embedding/import`
- upload flows with `auto_embed=true`
- Celery task `library.embed_pdf_book`

The trigger and PDF upload flows invoke Docling. The former PyMuPDF pipeline is
temporarily available as the non-UI rollback task `library.embed_pdf_book_old`
through `LegacyPdfIngestionService`.

Export and import operate on an already derived structured embedding and do not
invoke Docling. See [Librarian Embedding Packages](Embedding%20Packages.md).

## Active Pipeline

1. Resolve the library item, ontology, RPG system, and `content.pdf`.
2. Hash the source and acquire an expiring per-book Neo4j ingestion lock.
3. Parse locally with Docling's standard PDF pipeline.
4. Persist canonical JSON, debug Markdown, and a manifest.
5. Convert Docling objects into internal pages, sections, blocks, and provenance.
6. Build complete semantic parents and tokenizer-bounded retrieval children.
7. Build contextual `embedding_text`; retain separate verbatim `display_text`.
8. Embed children with the configured 384-dimensional `intfloat/multilingual-e5-small` model.
9. Stage a complete `PdfChunkCandidate` document graph and validate it.
10. Atomically switch staged chunks to the indexed `PdfChunk` label.
11. Mark the SQL `LibraryItem` vectorized and clean the retired version.

An ingestion failure never deletes or deactivates the previous successful graph.
If the SQL readiness update fails after graph activation, activation is compensated.

Re-embed every book after an embedding-model change. Vectors from the former
MiniLM model and E5 are not comparable, despite both using 384 dimensions.

## Local Parsing Configuration

Docling runs with remote services and external plugins disabled. The pipeline enables:

- layout and reading-order analysis;
- accurate table structure extraction;
- parsed-page retention and heading-level inference;
- page provenance and bounding boxes;
- structured picture blocks and their provenance, without exporting PNG assets;
- native PDF text extraction only; OCR is disabled entirely.

Docling model artifacts are prefetched into `DOCLING_ARTIFACTS_PATH` by the
existing Compose model-prefetch service. Runtime parsing therefore does not
require network access.

The prefetch service downloads `layout` and `tableformer` artifacts. OCR models
are not used by Librarian ingestion. See Docling's [offline model setup](https://docling-project.github.io/docling/usage/advanced_options/)
and [pipeline controls](https://docling-project.github.io/docling/reference/pipeline_options/).

Pictures remain `PdfBlock` records with page, bounding-box, reading-order, and
caption/text associations, but the worker does not export page or picture PNGs:
they are not used by current retrieval or synthesis. This avoids duplicate
Docling assets consuming hundreds of megabytes per book.

After parsing, the worker logs `parse_quality_summary`: native versus Docling
page counts, pages without structured blocks, detected tables/lists/pictures,
empty table/list blocks, and explicit Docling conversion diagnostics. Docling
does not provide a reliable authoritative count of tables or lists it failed to
recognize, so the summary does not guess; it reports only explicit diagnostics
and observable empty structured output.

## Optional NVIDIA GPU Acceleration

The default Compose deployment remains CPU-only. On a host with the NVIDIA
Container Toolkit, start the optional overlay to expose one GPU to the API,
Celery worker, and model-prefetch container and install CUDA PyTorch:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

The overlay sets `SHRECKNET_EMBEDDING_DEVICE=cuda`. E5 embeddings use CUDA and
Docling's layout/table models can select the exposed CUDA accelerator. If CUDA
is requested but unavailable, the embedding runtime logs a warning and falls
back to CPU rather than failing an ingestion.

## Filesystem Artifacts

Each attempt creates:

```text
media_root/library/{ontology_id}/{library_item_id}/
  content.pdf
  parsed/{ingestion_id}/
    docling_document.json
    document.md
    ingestion_manifest.json
```

`docling_document.json` is canonical. Markdown is only for inspection.
The manifest records source hash, parser/model versions, status, warnings,
statistics, and activation metadata. Writes use temporary files and atomic rename.
Failed attempt artifacts remain for diagnosis; retired successful artifacts are removed.

## Normalized Document Model

Docling classes stop at the adapter boundary. Internal dataclasses preserve:

- physical page position and displayed label as separate values;
- nested sections and complete heading paths;
- headings, paragraphs, lists/items, tables, pictures, captions, formulas,
  code, key/value regions, headers, footers, and unknown blocks;
- reading order, Docling references, page provenance, and bounding boxes.

Headers and footers are stored as blocks but excluded from embeddings. Pictures
retain original captions/nearby caption associations; no visual caption model
or image export is used.

## Parent and Child Chunks

Parents contain complete section or section-preamble display content and are
full-text eligible but not embedded. Every retrievable child has exactly one
`CHILD_OF` parent and ordered `DERIVED_FROM` block relationships.

Children follow structural boundaries:

- a complete section or RPG entry becomes one child when its full contextual
  representation fits the model budget;
- otherwise prose splits recursively at paragraph or sentence boundaries;
- tables use row groups and repeat table headers;
- lists use item groups;
- captioned pictures use caption text;
- textless pictures remain graph blocks without an embedding.

The initial content taxonomy is deliberately generic: `section`,
`section_preamble`, `narrative`, `table`, `list`, `picture`, `formula`, `code`,
`key_value`, and `unknown`.

## Display and Embedding Text

`display_text` is original user-visible evidence. The compatibility property
`text` contains the same value. `embedding_text` deterministically begins with
`passage:` and adds book, RPG system, heading path, entry, and content type
context. Query embedding uses `query:`. Only embedding text normalizes common
mathematical glyphs, and contextual prefixes are never cited.

Token counts use the model's actual tokenizer and include the contextual and
`passage:` prefixes. Children normally target 300–400 tokens; complete coherent
units may reach 500 tokens, never more than the model's real limit. Oversized
content is recursively split at structural boundaries: prose goes from blocks
to paragraphs to sentences; lists split at items; and tables split into row
groups while repeating their headers. If an individual structural unit still
does not fit (for example, one exceptionally long sentence or table cell), the
service uses explicit E5-tokenizer windows with a small overlap. Every window
is decoded, rebuilt with its full contextual prefix, counted again, and reduced
until it fits. This is a visible fallback in the worker log, never model-side
truncation. One oversized unit therefore cannot abort an otherwise valid book.

The service validates all children once after construction and again immediately
before each inference batch. It rejects/skips a malformed final child rather
than submitting it truncated; it checks the `passage:` prefix, the actual model
limit, non-finite vectors, and a vector dimension of 384. A book still requires
at least one valid child to activate.

## Runtime Progress and Job Reset

The worker emits `[LIBRARIAN_EMBED]` console events for task receipt, source
resolution, parse start/completion, every normalized page, chunk validation,
embedding batch start/completion (including page coverage), graph activation,
and completion. Percentages cover the complete book pipeline rather than only
the final vector call. Docling parsing runs in a worker thread and emits a
five-second `parse_heartbeat` while it is running. Docling's public one-shot
`convert()` API does not expose completed-page callbacks, so this phase is
explicitly **indeterminate**, not a made-up page percentage. Its heartbeat
includes the native PDF's total page count, elapsed time, and CUDA memory
snapshot when a GPU is active. Real page percentages resume during normalized
page construction, followed by chunk-batch percentages. The same fields are
written to the background job's visible status/progress record.

`Finished converting document` is a Docling message for the GPU parse only. It
is followed by lossless JSON export, debug-Markdown export, referenced-picture
export, and primitive-item extraction. The worker logs each of these as
`docling_convert_complete`, `canonical_json_written`,
`debug_markdown_written`, periodic `picture_export`, and
`docling_artifact_export_complete`; a large illustrated rulebook can spend
meaningful time in this post-conversion stage.

On a first structured ingestion, there is no `PdfDocument` graph yet. The
idempotency lookup deliberately uses dynamic Neo4j property access so that this
normal empty-state check does not produce unknown-label/property warnings.

Starting either the API or Celery worker performs a destructive job reset:

- all persisted background-job records are deleted;
- known active Celery tasks are revoked;
- `ontology_linking`, `architect`, and `celery` queues are purged;
- Redis reserved (`unacked`) task entries are removed so they cannot reappear;
- stale `PdfIngestionLock` nodes are deleted.

`DELETE /libraries/admin/clear-all-embeddings` performs the same global job and
broker reset in addition to deleting the selected embedding graph. This is
intentionally global because the shared Celery broker cannot safely purge one
embedding queue while retaining unrelated queued work. Do not use it if other
background work must be preserved.

## Neo4j Graph and Activation

The graph contains at least:

- `LibraryItem` - `HAS_DOCUMENT` -> `PdfDocument`
- `PdfDocument` - `HAS_PAGE` -> `PdfPage`
- `PdfDocument` - `HAS_SECTION` -> `PdfSection`
- nested `HAS_SUBSECTION` sections
- `PdfSection` - `CONTAINS_BLOCK` -> `PdfBlock`
- ordered `NEXT_BLOCK` relationships
- `PdfSection` - `HAS_PARENT_CHUNK` -> parent `PdfChunk`
- child `PdfChunk` - `CHILD_OF` -> parent `PdfChunk`
- chunk/block `ON_PAGE` provenance and chunk `DERIVED_FROM` blocks

New chunks are created as `PdfChunkCandidate`, outside the vector/full-text
indexes. Validation checks counts, parent links, source blocks, unique IDs, and
finite vectors of the configured dimension. One transaction retires the old
`PdfChunk` label, activates the candidate label, and switches `PdfDocument.is_active`.

Retrieval accepts active child chunks only. Legacy chunks without version fields
remain readable until the first successful Docling ingestion.

Ingestion history is deliberately not stored in SQL. The filesystem manifest and
temporary/versioned graph metadata provide operational state and diagnostics.
