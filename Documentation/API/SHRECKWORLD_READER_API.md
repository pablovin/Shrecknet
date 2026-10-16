# ShreckWorld reader and rule-review API

## Purpose and access

Every authenticated Shrecknet principal can discover ShreckWorlds and read their sources. The current policy exposes all knowledge worlds to authenticated users; this is not anonymous publication and there is no per-world membership ACL. Management still requires `admin` or `world_builder`. The identity bridge and data ownership remain defined in [ShreckWorld architecture](../Architecture/SHRECKWORLD.md).

| Method and route | Response |
| --- | --- |
| `GET /shreckworlds` | Complete array of knowledge worlds, ordered by name |
| `GET /shreckworlds/{world_id}` | One knowledge world |
| `GET /shreckworlds/{world_id}/library-items` | Complete source array, ordered by configured priority/title |
| `GET /shreckworlds/{world_id}/library-items/{item_id}` | One source, including SHA-256 revision, MIME type, filename and indexing state |
| `GET` / `HEAD /shreckworlds/{world_id}/library-items/{item_id}/content` | Original file, with authenticated single-byte-range support |
| `POST /shreckworlds/{world_id}/query` | Extractive answer and anchored evidence |
| `POST /shreckworlds/{world_id}/resolve-action` | Read-only evidence and conservative source-priority review |

IDs are ShreckWorld service IDs, not Shrecknet World IDs. Source lookups must match the supplied world. List filtering/sorting is currently performed by the frontend; these endpoints are not paginated. Existing `/admin` routes and old query payloads remain compatible.

## Original-file transport

Content requests send the Shrecknet bearer token in `Authorization`. Original files are never mounted as public media. Responses include `Cache-Control: private, no-store`, an ETag derived from `source_sha256`, and `Accept-Ranges: bytes`. A single `Range: bytes=start-end`, open-ended range, or suffix range returns `206` with `Content-Range`; unsatisfiable, malformed and multiple ranges return `416`. A mismatched `If-Range` returns the full current file. `HEAD` supplies headers without a body. The response reads bounded 64 KiB chunks; the reader can request partial content without first loading a full Blob. Authentication and source/world checks run before reading any bytes.

PDF.js receives original PDFs. DOCX/Markdown/text remain original downloads; this API does not generate PDF reading renditions. Indexing and model readiness do not gate discovery or reading.

## Evidence questions and selected passages

`POST /shreckworlds/{world_id}/query` accepts the existing `query`, `top_k` (1–20, default 6), and `include_trace`, plus optional source scope and selection:

```json
{
  "query": "Which skill applies here?",
  "library_item_id": "SOURCE_ID",
  "selection": {
    "library_item_id": "SOURCE_ID",
    "source_sha256": "64-character-source-sha256",
    "page_number": 12,
    "text": "The passage selected by the reader."
  }
}
```

Omit `library_item_id` for whole-world retrieval. Selection text is limited to 4,000 characters, the physical page number must be positive, and the selection revision must match the current source. A selected source must belong to the requested world and, when scoped to a book, to that book. Selection is untrusted retrieval context, not a model instruction or verified authoritative quote. No generated conversational synthesis is promised: the endpoint returns retrieved original passages with server-produced citations. The semantic encoder can reject queries exceeding its actual token budget; do not silently truncate quoted evidence or invent a result.

Citations retain `source_id`, `library_item_id`, `title`, `page_number`, `chunk_id`, score, excerpt and authority metadata. Additive fields include `source_sha256`, document/parent IDs, printed `page_label`, page ranges, headings, `parent_excerpt`, evidence text, and provenance. When reliable page dimensions/coordinate origin are available, `rectangles` supplies `{page_number,left,top,width,height}` in top-left percentages (0–100). Rotation is applied by the reader's viewport transform. Missing or ambiguous geometry must not be represented as an exact match; old page-only citations still navigate. Changing the source revision invalidates old highlight anchors. There is no historical-file archive.

## Rule review and agents

`POST /shreckworlds/{world_id}/resolve-action` accepts:

```json
{"action":"Attack while mounted", "facts":["The rider is charging"], "library_item_id":"SOURCE_ID"}
```

`action` is 1–2,000 characters. `facts` contains at most 20 nonblank strings of at most 500 characters. Omit source scope to consider the world. The same authenticated endpoint is available to UI and agent callers and delegates retrieval to the shared knowledge service.

The response contains `status`, `policy_revision`, `explanation`, citations, `preferred_source_ids`, `conflicting_source_ids`, `assumptions`, and `missing_facts`. The current policy is `source-priority-v1`: lower configured source priorities identify preferred evidence. Competing sources are potential conflicts, not a claim that their rules contradict. Authority-tier names alone do not establish precedence. With no evidence it returns `unsupported`; with evidence it returns `ambiguous` and requests applicability/ruleset/edition/override confirmation. The `resolved` status is reserved for a future explicitly modeled applicability policy and is not emitted by this version.

Rule review never executes game actions, rolls dice, or mutates character/game data. ShreckRPG remains responsible for validating and applying game actions. Existing Shrecknet agents are not automatically rewired. Agent clients can use the typed Python SDK `sdk.shreckworld.query(...)` and `sdk.shreckworld.resolve_action(...)` with the caller's current token; see [SDK reader workflow](../../python_sdk/docs/core_workflows/shreckworld-reader.md).

## Errors and operations

- `401`: missing/invalid Shrecknet authentication; `403`: insufficient role for management.
- `404`: missing world/source or a source belonging to another world; deleted/unavailable original file.
- `409`: selected document revision changed; reopen the source.
- `422`: invalid payload, cross-book selection, or encoder token budget exceeded.
- `503`: semantic inference unavailable. Original-file reading remains available.

Deploy this service API before enabling the reader frontend. These additive reader endpoints and fields need no database migration. Independent structured-ingestion/model changes may require reindexing; see the architecture/indexing guides. Rolling back the frontend or these reader routes preserves uploaded source files and management data. Unit tests use explicit model doubles and do not download model artifacts; real retrieval quality and mobile large-book performance require representative acceptance datasets/devices.

## Structured semantic generations

The reader queries the same verified Docling/E5 index described in the
[indexing API](SHRECKWORLD_INDEXING_API.md). Active generations must match the
current model fingerprint. Legacy/hash and incompatible sources remain readable
as files but require reindexing for evidence retrieval; query trace reports
`sources_requiring_reindex`. The pipeline uses scoped Qdrant/SQL semantic vectors,
contextual FTS5, exact named terms, shared RRF/diversity, and an optional local
cross-encoder. Its fallback preserves RRF order and is explicit in trace.

`provenance` includes source block ID, physical page, printed label, dimensions,
coordinate origin/bounding box, and available character spans. `rectangles`
identifies block regions; word-level quote alignment is not guaranteed. Unknown
origins/dimensions produce no guessed geometry. `evidence_text` carries bounded
parent/window display evidence and `content_url` anchors the authenticated source.
E5/contextual prefixes are never cited. Selected-passage and book-scope contracts
remain unchanged; semantic token-budget errors return `422`.
