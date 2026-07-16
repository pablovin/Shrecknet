# Librarian Retrieval

This document is the implementation reference for Librarian PDF retrieval,
parent expansion, synthesis evidence, provenance, and cleanup behavior.

## Strategy Selection

`librarian_retrieval_strategy` selects the runtime implementation:

- `v2` is the production default.
- `legacy` restores the former multi-query additive hybrid strategy.

The public Librarian query API does not expose strategy selection. This keeps
rollback under server configuration control.

## Retrieval v2 Pipeline

For each ontology attached to the Librarian agent, v2 performs the following
pipeline:

1. Preserve the original user query, including capitalization and named terms.
2. Add the E5 `query: ` prefix exactly once and generate one query embedding.
3. Run three independent Neo4j searches concurrently, each using its own
   session.
4. Merge results by stable `chunk_id` using Reciprocal Rank Fusion.
5. Rerank the best fused candidates with a local cross-encoder.
6. Select a small, diverse set of child chunks.
7. Expand each selected child through `CHILD_OF` to synthesis evidence.
8. Synthesize only from parent or sibling-window `display_text`.
9. Resolve model citations through stable server-assigned source IDs.

### Concurrent Retrieval Branches

Each branch returns at most 40 active child chunks.

Vector branch:

- Index: `pdf_chunk_text_vec_idx`
- Property: `text_embedding`, generated during ingestion from `embedding_text`
- The index is probed with a wider internal window before applying scope
  filters and returning the best 40 eligible results.

Contextual full-text branch:

- Index: `pdf_chunk_context_fulltext_v2_idx`
- Indexed properties:
  - `book_title`
  - `rpg_system`
  - `heading_path_text`
  - `primary_heading`
  - `display_text`
- Search terms are Lucene-escaped and joined without changing the original
  query used for embedding and reranking.

Exact named-term branch:

- Extracts quoted concepts and deterministic capitalized named terms.
- Prioritizes exact `primary_heading` matches.
- Also matches named terms inside `heading_path_text` and `display_text`.
- Provides a retrieval path for named rules or concepts that semantic search
  may rank poorly.

All branches enforce:

- `is_active = true`
- `chunk_role = 'child'`
- matching `ontology_id`
- SQL `vectorized = true` membership through `active_library_item_ids`
- optional `library_item_ids` requested by the caller
- applicable embedding/full-text eligibility properties

An empty active-item list returns no evidence without querying stale chunks.

### Reciprocal Rank Fusion

Branch results are deduplicated by `chunk_id` and combined using:

`RRF score = sum(1 / (60 + branch_rank))`

Only rank positions contribute to fusion. Vector similarity, Lucene score, and
exact-match score are never normalized together, added together, or compared
as though they shared a scale. Ties are resolved deterministically by
`chunk_id`.

### Cross-Encoder Reranking

The best 25 RRF candidates are reranked with:

- Model: `cross-encoder/ms-marco-MiniLM-L-6-v2`
- Runtime: `sentence-transformers` on the configured embedding device
- Pair input: original user query and child evidence augmented with book and
  heading context

The model is loaded lazily. If loading or prediction fails, retrieval remains
available and preserves deterministic RRF order. Candidates outside the
25-item rerank window retain their RRF ordering.

### Diversity Selection

An explicit request `top_k` is clamped to an effective v2 range of 5–8. When
the request omits `top_k`, v2 selects six. Legacy retrieval continues using the
server-wide `default_top_k`. If fewer candidates exist, all eligible candidates
are retained.

Selection proceeds greedily by reranked relevance while:

- suppressing duplicate chunk IDs;
- suppressing near-duplicate evidence text;
- preferring one matched child per semantic parent;
- limiting repeated evidence from one book when multiple books are available.

If strict diversity would produce too few results, the selector fills the
remaining positions in relevance order. Exclusions and the effective top-k are
recorded in retrieval trace data.

## Parent Semantic Expansion

Selected retrieval hits are always child chunks. V2 follows each child's
`CHILD_OF` relationship before synthesis.

Normal parents:

- Return the complete parent `display_text`.
- Retain the matched child's identity and text as provenance metadata.

Large parents:

- Parents above 12,000 characters use `sibling_window` expansion.
- Start with the matched child and add ordered siblings on both sides.
- Stop after reaching the current 8,000-character sibling-window target or
  exhausting the parent's children.
- This prevents an entire oversized chapter from displacing other evidence.

List and table questions:

- Keep the complete list/table parent instead of constructing a sibling
  window.
- Parents above 40,000 characters are marked `incomplete_evidence` for safe
  synthesis handling.
- The structured context response retains the complete parent, while natural
  language synthesis receives an explicit warning and must not invent or
  present a silently truncated list.

`embedding_text` is retrieval-only. It must never be included in the synthesis
prompt. Synthesis uses only expanded `display_text` or the explicit
incomplete-evidence warning.

## Source IDs, Pages, and Bounding Boxes

Each final evidence unit receives a stable request-local identifier such as
`source-1`. The answer model cites only that identifier:

`[supported text]{cite source_id=source-1}`

The server resolves the identifier to trusted metadata rather than accepting
model-authored book or page values. Returned `chunks` and `sources_used`
include:

- `source_id`
- `chunk_id` and `parent_chunk_id`
- `physical_page_numbers`
- `displayed_page_labels`
- primary `display_page_label`
- `bounding_boxes`
- `matched_child_text`
- `expansion_mode`
- `incomplete_evidence`
- PDF and page navigation URLs

Rendered citations prefer the displayed PDF page label. When no displayed
label exists, they fall back to the physical page number. Bounding boxes remain
available as structured provenance for clients that support page highlighting.
Legacy library-item/page citation wrappers remain readable for backward
compatibility.

## Index Bootstrap

`PdfEmbeddingService.ensure_vector_index()` maintains:

- vector index `pdf_chunk_text_vec_idx`;
- legacy full-text index `pdf_chunk_text_fulltext_idx` over `text`;
- v2 full-text index `pdf_chunk_context_fulltext_v2_idx` over the five
  contextual properties.

The v2 strategy also creates its full-text index idempotently before retrieval.
The legacy index is intentionally retained so configuration rollback does not
require an index migration.

## Trace and Debug Data

When tracing is enabled, v2 records:

- original and prefixed embedding queries;
- extracted named terms;
- candidate counts for all three branches;
- RRF constant and fused candidate count;
- rerank window size and fallback status;
- effective final top-k and diversity exclusions;
- parent expansion modes for selected evidence.

Existing Librarian debug artifacts also capture the request scope, final
retrieval selection, synthesis context, citation resolution, and response.

## Legacy Retrieval

`LegacyLibrarianRetrievalStrategy` preserves the former behavior:

- heuristic multi-query planning;
- vector search through `PdfEmbeddingService.search_chunks(...)`;
- fuzzy full-text search through `pdf_chunk_text_fulltext_idx`;
- additive vector, full-text, and lexical scoring;
- optional dynamic score floors and per-book caps;
- page-anchor and neighboring-page expansion for broad list/table requests;
- optional neighbor enrichment when fast mode is disabled.

Legacy retrieval exists only as a rollback path. New retrieval quality changes
should target v2 and must not reintroduce raw-score addition there.

## Clear and Cleanup Operations

Implemented deletion operations:

- `delete_embeddings(library_item_id)`
- `delete_embeddings_for_ontology(ontology_id, library_item_ids?)`
- `delete_all_embeddings()`
- `delete_orphan_embeddings(valid_library_item_ids)`

`DELETE /libraries/admin/clear-all-embeddings`:

1. Deletes Neo4j PDF chunks in the requested ontology scope, or globally when
   no ontology is supplied.
2. Runs orphan cleanup for chunks not tied to valid SQL library items.
3. Resets affected SQL `vectorized` flags.
4. Removes queued or running embedding jobs for the same scope.

Stale-data protection combines active-version labels, `is_active` checks,
SQL-vectorized item filtering, ontology scoping, hard deletion on PDF
replacement, and atomic Docling candidate activation.

## Verification Coverage

Focused tests cover:

- exact-once query prefixing and named-term preservation;
- concurrent branch execution;
- rank-only RRF behavior under incompatible raw-score ranges;
- duplicate merging and exact-only candidates;
- bounded reranking and deterministic fallback;
- 5–8 result selection and diversity behavior;
- complete-parent, sibling-window, and list/table expansion;
- `display_text`-only synthesis;
- page-label, bounding-box, and stable-source citation provenance;
- v2/legacy strategy selection and index definitions.
