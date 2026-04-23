# Librarian Retrieval

This document describes Neo4j retrieval and embedding cleanup operations for Librarian.

## Vector Retrieval

Primary search API:

- `PdfEmbeddingService.search_chunks(...)`

Mechanics:

- Embed query text
- Query Neo4j vector index `pdf_chunk_text_vec_idx`
- Apply filters:
  - `c.ontology_id = $ontology_id`
  - optional user filter `library_item_ids`
  - active safety filter `active_library_item_ids` (vectorized-only SQL IDs)
  - similarity threshold
- Return top scored chunk metadata and navigation URLs

## Neighbor Enrichment

Optional enrichment (`fast_mode = false`):

- `enrich_chunks_with_neighbors(...)`
- Appends adjacent page text while preserving main citation page

## Clear and Cleanup Operations

Implemented delete operations:

- `delete_embeddings(library_item_id)`
- `delete_embeddings_for_ontology(ontology_id, library_item_ids?)`
- `delete_all_embeddings()`
- `delete_orphan_embeddings(valid_library_item_ids)`

### Clear-All Endpoint Behavior

`DELETE /libraries/admin/clear-all-embeddings` now:

1. Deletes by ontology directly in Neo4j when scoped
2. Deletes globally when no ontology is provided
3. Runs orphan cleanup pass for chunks not tied to SQL `library_items`
4. Resets SQL vectorized flags for affected items
5. Removes queued/running embedding jobs for same scope

## Stale Data Risk Control

Stale retrieval risk is mitigated by combining:

- hard delete before each re-embed
- hard delete on PDF replacement
- strict vectorized-only retrieval filters
- ontology-scoped retrieval predicates
