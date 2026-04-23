# Librarian Agent

This document describes Librarian in production scope.

## Jobs Overview

Librarian is a PDF-grounded question answering pipeline over embedded `PdfChunk` nodes in Neo4j.

Main endpoint:

- `POST /jobs/librarian/{agent_id}/query`

## Goal

Given a user question, Librarian retrieves relevant book excerpts and returns:

- grounded answer text
- source chunks
- library item usage metadata
- page-precise navigation links (`pdf_url`, `page_url`)

## Runtime Pipeline

Current pipeline stages:

1. Query validation and agent scope loading
2. Retrieval from Neo4j vector index (`pdf_chunk_text_vec_idx`)
3. Optional chunk context enrichment (neighbor pages when fast mode is off)
4. LLM answer synthesis with inline citation wrappers
5. Source extraction and response assembly

## Freshness and Safety Guarantees

Librarian now enforces hard freshness semantics:

- Every embed run deletes prior chunks for that `library_item_id` before writing new chunks.
- Replacing a PDF file deletes old chunks immediately and marks item as unvectorized.
- Retrieval only uses SQL items with `vectorized = true`.
- If embedding fails, item is forced to `vectorized = false` (no stale fallback path).

## Source and Citation Behavior

Retrieved chunks carry:

- `library_item_id`
- `page_number`
- `text`
- `score`
- `pdf_url`
- `page_url`
- `book_title`
- `book_authors`

Answer generation is instructed to wrap sourced text with cite wrappers:

- `[text]{cite library_item_id=ID library_item_name="BOOK_TITLE" page=PAGE}`

`sources_used` is derived by parsing cited `(library_item_id, page)` pairs and matching returned chunks.

## Related Docs

- Embedding: [Embedding.md](Embedding/Embedding.md)
- Querry: [Querry.md](Querry/Querry.md)
- Retrieval: [Retrieval.md](Retrieval/Retrieval.md)
