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
| `POST /shreckworlds/{world_id}/query` | Backward-compatible single retrieval or opt-in planned evidence |
| `POST /shreckworlds/{world_id}/agent-jobs` | Queue a ShreckWorld-owned planned evidence query |
| `GET /shreckworlds/{world_id}/agent-jobs/{job_id}` | Poll that agent job and read its completed result |
| `GET /admin/settings/model-targets/shreckworld_synthesizer` | Read the shared ShreckWorld LLM target |
| `PUT /admin/settings/model-targets/shreckworld_synthesizer` | Update the shared ShreckWorld LLM target |
| `POST /shreckworlds/{world_id}/resolve-action` | Read-only evidence and conservative source-priority review |

IDs are ShreckWorld service IDs, not Shrecknet World IDs. Source lookups must match the supplied world. List filtering/sorting is currently performed by the frontend; these endpoints are not paginated. Existing `/admin` routes and old query payloads remain compatible.

## Original-file transport

Content requests send the Shrecknet bearer token in `Authorization`. Original files are never mounted as public media. Responses include `Cache-Control: private, no-store`, an ETag derived from `source_sha256`, and `Accept-Ranges: bytes`. A single `Range: bytes=start-end`, open-ended range, or suffix range returns `206` with `Content-Range`; unsatisfiable, malformed and multiple ranges return `416`. A mismatched `If-Range` returns the full current file. `HEAD` supplies headers without a body. The response reads bounded 64 KiB chunks; the reader can request partial content without first loading a full Blob. Authentication and source/world checks run before reading any bytes.

PDF.js receives original PDFs. DOCX/Markdown/text remain original downloads; this API does not generate PDF reading renditions. Indexing and model readiness do not gate discovery or reading.

## Evidence questions and selected passages

`POST /shreckworlds/{world_id}/query` accepts the existing `query`, `top_k` (1–20, default 6), and `include_trace`, plus optional source scope, response mode, and selection:

```json
{
  "query": "Which skill applies here?",
  "library_item_ids": ["SOURCE_ID_A", "SOURCE_ID_B"],
  "mode": "answer",
  "selection": {
    "library_item_id": "SOURCE_ID_A",
    "source_sha256": "64-character-source-sha256",
    "page_number": 12,
    "text": "The passage selected by the reader."
  }
}
```

Omit both source-scope fields for whole-world retrieval. `library_item_id` remains supported for existing callers; new callers can pass `library_item_ids` to select 1–100 distinct sources. They are mutually exclusive. Every ID must belong to the requested ShreckWorld. When a source scope is present, a selected passage must belong to that scope. Selection text is limited to 4,000 characters, the physical page number must be positive, and the selection revision must match the current source. Selection is untrusted retrieval context, not a model instruction or verified authoritative quote.

`mode` is `evidence` (default, backward-compatible) or `answer`. Evidence mode performs no synthesis and retains the current extractive response. Answer mode sends the retrieved evidence to the configured ShreckLLM synthesis model and returns `answer_status` (`complete`, `partial`, or `unsupported`), structured `claims`, `missing_information`, and `conflicts`. Each claim/conflict must cite source IDs returned with the response; invalid or unknown citation IDs fail the request rather than being silently removed. The server validates output structure and citation membership, but semantic entailment of every claim is not independently proven by a second model call. Answers are marked partial and disclose coverage gaps when requested sources lack compatible indexes or retrieved passages are omitted by the evidence budget. Rule priority is not treated as proof of applicability or precedence. Synthesis does not execute game actions.

`retrieval_strategy` is `single` (default) or `planned`. `single` preserves the established retrieval and answer-mode behavior. `planned` is opt-in and accepts `mode="evidence"` only; combining `planned` with `mode="answer"` returns `422`. Planned retrieval builds a 1–6 need `query_plan`, searches each need against the same authorized active-generation scope, retains evidence by document/section without merging distinct sources, and includes `evidence_coverage`, `evidence_groups`, and optional `v3_trace`. Complex questions can invoke one ShreckLLM planning call; simple definitions use a deterministic single-need plan. An invalid or unavailable planner falls back to a safe deterministic need and is reported in `v3_trace.planner_fallback`.

```json
{
  "query": "Can psychotherapy and private care be combined?",
  "library_item_ids": ["SOURCE_ID_A", "SOURCE_ID_B"],
  "mode": "evidence",
  "retrieval_strategy": "planned",
  "include_trace": true
}
```

Planned retrieval does not generate an answer. `answer` explains that the response is an evidence bundle, while `citations` retains the normal source revision, page, geometry, and content URL fields. Coverage distinguishes `covered`, `partial`, `missing`, and `unresolved` per need. Retrieval similarity alone never establishes coverage. When enabled, one batched ShreckLLM review checks direct source quotations; invalid review output is not treated as verification. A single targeted follow-up round may search needs still missing, partial, or unresolved. Exhaustive questions and unsupported interactions are not declared complete merely because results were found. When any requested source is unindexed or incompatible, overall evidence coverage cannot be `adequate`; `source_coverage` distinguishes those unsearched sources from sources actually searched. The configurable budget is approximate (4 characters per token); a logical evidence unit that cannot fit is omitted whole and flagged in `evidence_groups` rather than silently truncated.

Each returned `evidence_groups[].evidence_id` matches its citation's `source_id`. An omitted oversized unit has an `omitted-{chunk_id}` evidence ID, no citation, `incomplete_evidence: true`, and an `omission_reason`; its need association remains visible.

## ShreckWorld agent jobs

`POST /shreckworlds/{world_id}/agent-jobs` accepts the query fields above, including optional `library_item_id` or `library_item_ids`, `selection`, `top_k`, and `include_trace`. The endpoint always creates a ShreckWorld planned-evidence job; `mode="answer"` is rejected with `422`. The authenticated user's identity is recorded as the job owner. The service validates world/source scope and selected-source revision before queueing, then validates it again when the worker executes.

The response is `202 Accepted` with `id`, `shreckworld_id`, `status`, `stage`, and timestamps. `GET /shreckworlds/{world_id}/agent-jobs/{job_id}` returns the same lifecycle fields, plus `result` when complete or a user-safe `error` when failed. Status is `queued`, `running`, `completed`, or `failed`; stages identify planning, retrieval, coverage assessment, targeted follow-up, reassessment, or completion. Only the job owner or an `admin` may poll it; an inaccessible or mismatched job returns `404`. Jobs are not cancellable. The existing synchronous query endpoint remains available and unchanged by job submission.

The `shreckworld_agent` Celery queue is consumed by the ShreckWorld worker alongside `shreckworld_embedding` with the worker's existing single-task concurrency bound. Jobs persist in `shreckworld_agent_jobs`; startup creates the table for existing installations. Queue-dispatch failures are marked failed and return `503`; execution failures are logged server-side and exposed as a generic retryable job error. The agent does not call Elder or Librarian runtimes and does not run grounded answer synthesis.

`source_coverage` is always returned, including in evidence mode. Its counts distinguish requested sources, sources with retrievable active chunks, sources without a ready index, and ready sources that require reindexing for the active embedding fingerprint. Only indexed compatible sources are searched. A source may be omitted from results because it has no ready index or needs reindexing; `include_trace` adds retrieval diagnostics (`retrieval_ms`, `synthesis_ms` when answer synthesis runs, and `total_ms`) but does not change scope. Timings are request-level observations, not latency guarantees.

The response also contains citations with original passages and server-produced provenance. The semantic encoder can reject queries exceeding its actual token budget; do not silently truncate quoted evidence or invent a result.

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
- `503`: agent-job queue dispatch failed; the persisted job is marked failed.
- Agent job execution errors are available as a safe `error` on the owner-visible job resource.
- `503`: ShreckLLM unavailable or synthesis deadline exceeded in `mode="answer"`.
- `502`: synthesis returned malformed structured output or invalid citation references.
- `422`: no complete passage fits the configured synthesis evidence budget.

Deploy this service API before enabling the reader frontend. The service's existing schema initialization creates the new model-settings table automatically; it requires no data backfill. Independent structured-ingestion/model changes may require reindexing; see the architecture/indexing guides. Rolling back the frontend or these reader routes preserves uploaded source files and management data. Unit tests use explicit model doubles and do not download model artifacts; real retrieval quality and mobile large-book performance require representative acceptance datasets/devices.

For ShreckLLM, configure `SHRECKWORLD_SHRECKLLM_BASE_URL`. The ShreckWorld
model target is managed through the settings API below; the legacy
`SHRECKWORLD_SYNTHESIS_PROVIDER` and `SHRECKWORLD_SYNTHESIS_MODEL` values only
provide its initial default before a target is saved.
`SHRECKWORLD_SYNTHESIS_TIMEOUT_SECONDS` controls the job deadline,
`SHRECKWORLD_SYNTHESIS_POLL_INTERVAL_SECONDS` controls status polling, and
`SHRECKWORLD_SYNTHESIS_EVIDENCE_TOKEN_BUDGET` caps complete evidence passages
supplied to synthesis (estimated at four characters per token). The optional
`SHRECKLLM_INTERNAL_SERVICE_TOKEN` is forwarded as a bearer token for
installations that protect internal ShreckLLM calls. Defaults are
`http://shreckllm:8110`, `openai`, `gpt-5-nano`, 25 seconds, 0.5 seconds, and
8,000 estimated evidence tokens respectively. The default deadline fits
within the Python SDK's 30-second request timeout; when increasing the server
deadline, increase the SDK `Shrecknet(timeout=...)` value accordingly. Evidence
mode and source reading do not require ShreckLLM availability.

## ShreckWorld model settings

The ShreckWorld provider/model target is service-owned and shared by the agent
planner, evidence-coverage reviewer, and grounded answer mode. Read or update it
with an `admin` or `world_builder` token:

```http
GET /admin/settings/model-targets/shreckworld_synthesizer
```

```json
{
  "model_shreckworld_synthesizer": {
    "provider": "openai",
    "name": "gpt-5-nano",
    "reasoning": false,
    "reasoning_effort": null
  }
}
```

Send the same object shape to `PUT` to save a new target. `provider` and `name`
must be non-empty strings; `reasoning` defaults to `false`, and
`reasoning_effort` can be `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`.
The target is persisted in the ShreckWorld database and takes precedence over
the legacy `SHRECKWORLD_SYNTHESIS_PROVIDER` and
`SHRECKWORLD_SYNTHESIS_MODEL` environment defaults. Until a target is saved,
those variables (defaulting to `openai` and `gpt-5-nano`) provide the initial
value. Changing this LLM target does not require reindexing.

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
