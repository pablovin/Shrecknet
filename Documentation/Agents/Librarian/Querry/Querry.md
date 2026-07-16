# Librarian Querry

This document describes the Librarian query API behavior.

Retrieval v2 responses expose stable `source_id`, child and parent chunk IDs,
physical pages, displayed page labels, bounding boxes, matched-child text, and
the parent expansion mode. Rendered citations prefer displayed page labels and
fall back to physical page numbers.

## Endpoint

- `POST /jobs/librarian/{agent_id}/query`

Request model supports:

- `query`
- `mode` (`nl | context | both`)
- `top_k`
- `library_item_ids` (optional filter)
- `score_threshold` (optional)
- `include_trace`

## Response Shape

Main response fields:

- `agent_id`
- `mode`
- `query`
- `answer`
- `chunks`
- `sources_used`
- `library_items_used`
- `trace` (optional)

Each returned chunk includes frontend navigation fields:

- `pdf_url`
- `page_url`
- `page_number`

## Source Precision in Answer

Librarian answer generation requests inline cite wrappers:

- `[text]{cite library_item_id=ID library_item_name="BOOK_TITLE" page=PAGE}`

`sources_used` is extracted from cite wrappers and mapped to retrieved chunks.

## Freshness Rules During Query

Query path is now strict:

- Only `vectorized = true` SQL items are eligible for retrieval.
- Retrieval enforces ontology scoping (`c.ontology_id = $ontology_id`).
- Unvectorized items cannot contribute chunks, preventing stale pulls after PDF replacement or failed embed.
