# Librarian Embedding

This document describes PDF embedding for Librarian.

## Entry Points

Embedding is triggered by:

- `library.embed_pdf_book` Celery task
- Library endpoint trigger API
- Optional auto-embed flows after upload

Core files:

- `shrecknet/app/tasks/pdf_embedding.py`
- `shrecknet/app/services/pdf_embedding_service.py`

## Embed Flow (Current)

1. Resolve target `LibraryItem` and PDF path
2. **Delete old chunks for this item** (`delete_embeddings(library_item_id)`)
3. Ensure Neo4j vector index (`pdf_chunk_text_vec_idx`)
4. Extract and normalize PDF text
5. Build semantic chunks with page span metadata
6. Embed and write chunks to Neo4j (`MERGE` on `library_item_id + chunk_index`)
7. Verify post-write duplicate key count
8. Mark SQL item vectorized on success

## Hard Freshness Semantics

Mandatory behavior:

- Old chunks are deleted before every embed run.
- No version history is retained in `PdfChunk` storage.
- If embed fails after deletion, SQL item is explicitly set:
  - `vectorized = false`
  - `last_vectorized_at = null`

This guarantees no stale chunks are considered current.

## Stored Chunk Fields

`PdfChunk` stores:

- `library_item_id`
- `ontology_id`
- `chunk_index`
- `page_number`
- `primary_page_number`
- `start_page_number`
- `end_page_number`
- `page_numbers`
- `text`
- `text_embedding` (+ model/dim metadata)
- `last_embedded_date`

## Operational Logging

Embed task logs and job details now include:

- `deleted_old_chunks`
- `duplicate_chunk_keys`
- chunk creation/failed counters and page extraction stats
