# Librarian Querry

This document describes the Librarian query API behavior. Query v2 is the only
supported Librarian query pipeline.

## Query v2 State Machine

1. `model_librarian_planner` decomposes the original question into one to eight
   standalone information needs and detects its BCP-47 target language using
   strict JSON Schema when supported.
   Malformed output is repaired through `model_agents_repair_json`; unrecoverable
   output falls back to the unchanged original question.
2. Each need is searched against every eligible ontology with bounded
   concurrency. Retrieval uses the v2 vector/full-text/exact branches, RRF,
   reranking, diversity selection, and child-to-parent graph expansion.
3. Evidence is deduplicated by stable parent/chunk identity while retaining
   every matched need and retrieval-pass number.
4. Evidence is ranked once and admitted to the fixed synthesis budget.
5. `model_librarian_synthesis` returns neutral English atomic claims with
   trusted source IDs.
6. `model_librarian_character_incorporation` receives only the original query,
   detected language, agent name/description/style, and citation-free claims.
7. The character model composes cohesive passages associated with claim IDs.
   The backend requires every claim exactly once, derives `sources_used` from
   those associations, and appends Unicode superscript numbers in
   `sources_used` order. Invalid character output
   is repaired through `model_agents_repair_json`. If a configured character
   target remains invalid after repair, the request fails explicitly rather than
   presenting neutral synthesis as an in-character Librarian response.

`context` performs planning and retrieval but skips synthesis.
`nl` and `both` perform the complete pipeline.

The model targets are configured through `GET/PUT /config/`, exposed by
`GET /config/schema`, and reported by `GET /llm_status/` as
`model_librarian_planner`, `model_librarian_synthesis`, and
`model_librarian_character_incorporation`.

### Synthesis evidence budget

Before natural-language synthesis, consolidated evidence is ordered by retrieval
score and admitted up to an estimated `30,000` evidence-token budget. The next
complete evidence chunk is added first; when the accumulated context exceeds
30,000 tokens, collection stops and retains that crossing chunk. This preserves
source and citation boundaries
instead of truncating evidence mid-chunk.

The budget applies only to evidence sent to the synthesis model; `context` and
`both` responses retain the complete retrieved chunk set. The optional trace
includes `v2_synthesis_evidence_budget` with candidate and selected chunk
counts and the estimated evidence-token total.

When `include_trace=true`, ordered trace entries cover planning, per-need
retrieval, evidence merging, synthesis context, structured-output repair,
citation validation, and citation rendering. File-based local-test
artifacts are enabled by default. Setting
`librarian_debug_artifacts_enabled=true` writes numbered JSON snapshots plus
`manifest.json` beneath `databases/local_tests/librarian/querry_<timestamp>/`;
artifact failures never fail the user request.

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

Neutral Librarian synthesis assigns source IDs to atomic claims. Citation
identifiers never enter character incorporation. The character model may
reorder, combine, and condense claims while preserving every claim exactly once.
The backend appends plain-text Unicode superscript markers (`¹`, `²`, …, `¹⁰`)
according to the corresponding one-based entry in `sources_used`.

The server maps stable request-local source IDs to trusted book, page, URL, and
bounding-box metadata. Legacy library-item/page wrappers remain readable.

## Freshness Rules During Query

Query path is now strict:

- Only `vectorized = true` SQL items are eligible for retrieval.
- Retrieval enforces ontology scoping (`c.ontology_id = $ontology_id`).
- Unvectorized items cannot contribute chunks, preventing stale pulls after PDF replacement or failed embed.
