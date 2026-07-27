# Librarian Querry

This document describes the Librarian query API behavior. Query v2 is the only
supported Librarian query pipeline.

## Query v2 State Machine

1. An LLM decomposes the original question into one to eight standalone
   information needs, using every RPG system linked to the agent. Invalid
   output falls back to the unchanged original question.
2. Each need is searched against every eligible ontology with bounded
   concurrency. Retrieval uses the v2 vector/full-text/exact branches, RRF,
   reranking, diversity selection, and child-to-parent graph expansion.
3. Evidence is deduplicated by stable parent/chunk identity while retaining
   every matched need and retrieval-pass number.
4. An LLM checks coverage and identifies any missing information. Only novel
   missing needs are retrieved again. The pipeline permits an initial pass and
   at most two follow-up passes, and stops early on adequate coverage, repeated
   needs, or a pass that adds no evidence.
5. Natural-language modes synthesize once from the original question and the
   consolidated display evidence. Validator failure is fail-safe: collected
   evidence is retained and synthesis is instructed to disclose uncertainty.

`context` performs planning, retrieval, and validation but skips synthesis.
`nl` and `both` perform the complete pipeline.

### Synthesis evidence budget

Before natural-language synthesis, consolidated evidence is ordered by retrieval
score and admitted up to an estimated `30,000` evidence-token budget. If the
next complete evidence chunk crosses the budget, that one chunk is retained and
no later evidence is included. This preserves source and citation boundaries
instead of truncating evidence mid-chunk.

The budget applies only to evidence sent to the synthesis model; `context` and
`both` responses retain the complete retrieved chunk set. The optional trace
includes `v2_synthesis_evidence_budget` with candidate and selected chunk
counts and the estimated evidence-token total.

When `include_trace=true`, ordered trace entries cover planning, retrieval
passes and per-need results, evidence merges, coverage validation, retry
decisions, synthesis context, and citation rendering. File-based local-test
artifacts are disabled by default. Setting
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

Librarian answer generation requests inline cite wrappers:

- `[text]{cite source_id=source-N}`

The server maps stable request-local source IDs to trusted book, page, URL, and
bounding-box metadata. Legacy library-item/page wrappers remain readable.

## Freshness Rules During Query

Query path is now strict:

- Only `vectorized = true` SQL items are eligible for retrieval.
- Retrieval enforces ontology scoping (`c.ontology_id = $ontology_id`).
- Unvectorized items cannot contribute chunks, preventing stale pulls after PDF replacement or failed embed.
