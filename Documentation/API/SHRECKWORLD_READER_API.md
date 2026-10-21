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

## Browser origins

CORS is configured exclusively in the repository-root `.env`, mounted read-only
at `/app/browser.env`. Both APIs use the shared `shreckconfig` loader;
process environment values cannot override this policy. See the
[shared CORS guide](../Deployment/BROWSER_CORS.md) for syntax, container rollout,
restart behavior, and failure handling. Authentication remains required on source
and query routes.

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

`mode` is `evidence` (default, backward-compatible) or `answer`. Evidence mode performs no synthesis and retains the current response. Answer mode sends retrieved evidence to the configured ShreckLLM target and returns `answer_status` (`complete`, `partial`, or `unsupported`), structured `claims`, `answer_content`, `evidence`, `missing_information`, and `conflicts`. Every returned claim contains an ordered `claim-N` ID, source `citation_ids`, and exact-quote `support` records with a source location. `answer_content.direct_answer` and section text include inline `[source-N]` references; each ID maps to the same ID in `citations`, `claims[].support[].evidence_id`, and `evidence[].id`. The server validates structure and filters out any generated claim whose citations or supporting quotes cannot be verified against supplied evidence. If some claims are filtered, verified claims are returned with `answer_status="partial"`; if none remain, the answer is `unsupported`. Retrieved evidence remains available in `citations` and `evidence`. Semantic entailment of every surviving claim is not independently proven by a second model call. Answers are also marked partial when requested sources required for exhaustive, comparison, or multi-need coverage lack compatible indexes. Rule priority is not treated as proof of applicability or precedence. Synthesis does not execute game actions.

`retrieval_strategy` is `single` (default) or `planned`. `single` preserves the established retrieval and answer-mode behavior. `planned` builds a 1–6 need `query_plan`, searches each need against the same authorized active-generation scope, retains evidence by document/section without merging distinct sources, and includes `evidence_coverage`, `evidence_groups`, and optional `v3_trace`. Complex questions can invoke one ShreckLLM planning call; simple definitions use a deterministic single-need plan. An invalid or unavailable planner falls back to a safe deterministic need and is reported in `v3_trace.planner_fallback`. Both `mode="evidence"` and `mode="answer"` are supported; planned answer mode synthesizes once from the evidence selected by this same planned retrieval run, without a second retrieval.

```json
{
  "query": "Can psychotherapy and private care be combined?",
  "library_item_ids": ["SOURCE_ID_A", "SOURCE_ID_B"],
  "mode": "answer",
  "retrieval_strategy": "planned",
  "response_detail": "concise",
  "include_trace": true
}
```

`response_detail` is optional (`concise`, `standard`, or `detailed`) and applies to synthesis; planned mode defaults to `concise`, while legacy single-path answer mode defaults to `standard` for compatibility. Planned evidence mode does not generate an answer, while planned answer mode runs the same retrieval/coverage pipeline and then one grounded synthesis call. If no citation-ready evidence is found, answer mode returns `unsupported` without calling ShreckLLM. Source availability warnings are returned separately in `source_warnings`; they do not become answer claims. For exhaustive, comparison, and multi-need questions, unavailable requested sources prevent an otherwise complete synthesis from being labeled complete.

`citations` retains the normal source revision, page, geometry, and content URL fields. Coverage distinguishes `covered`, `partial`, `missing`, and `unresolved` per need. Retrieval similarity alone never establishes coverage. When enabled, one batched ShreckLLM review checks direct source quotations; invalid review output is not treated as verification. A single targeted follow-up round may search needs still missing, partial, or unresolved. Exhaustive questions and unsupported interactions are not declared complete merely because results were found. When any requested source is unindexed or incompatible, `source_coverage` distinguishes those unsearched sources from sources actually searched. The configurable budget is approximate (4 characters per token); a logical evidence unit that cannot fit is omitted whole and flagged in `evidence_groups` rather than silently truncated.

Example of the structured answer fields (source text and page are illustrative):

```json
{
  "mode": "answer",
  "retrieval_strategy": "planned",
  "answer_status": "complete",
  "answer": "Sanity measures a character's mental stability. [source-1]",
  "answer_content": {
    "direct_answer": "Sanity measures a character's mental stability. [source-1]",
    "sections": [],
    "follow_up_hint": null
  },
  "claims": [{
    "id": "claim-1",
    "text": "Sanity measures a character's mental stability.",
    "citation_ids": ["source-1"],
    "kind": "fact",
    "support": [{
      "evidence_id": "source-1",
      "quote": "Sanity is a measure of mental stability.",
      "location": {
        "precision": "block",
        "page_number": 12,
        "page_label": "10",
        "page_numbers": [12],
        "block_id": "block-abc",
        "charspan": null,
        "rectangles": []
      }
    }]
  }],
  "evidence": [{
    "id": "source-1",
    "source": {
      "library_item_id": "book-123",
      "title": "Core Rules",
      "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "authority_tier": "core_rules",
      "authority_priority": 10,
      "document_id": "document-456"
    },
    "location": {
      "precision": "block",
      "page_number": 12,
      "page_label": "10",
      "page_numbers": [12],
      "block_id": "block-abc",
      "charspan": null,
      "rectangles": []
    },
    "excerpt": "Sanity is a measure of mental stability.",
    "context_excerpt": null,
    "content_url": "/shreckworlds/world-1/library-items/book-123/content"
  }]
}
```

Each returned `evidence_groups[].evidence_id` matches its citation's `source_id`. An omitted oversized unit has an `omitted-{chunk_id}` evidence ID, no citation, `incomplete_evidence: true`, and an `omission_reason`; its need association remains visible.

## ShreckWorld agent jobs

`POST /shreckworlds/{world_id}/agent-jobs` accepts the query fields above, including optional `library_item_id` or `library_item_ids`, `selection`, `top_k`, `include_trace`, `mode`, and `response_detail`. It always creates a ShreckWorld planned-query job; `mode="answer"` requests grounded synthesis after the planned evidence stages. The authenticated user's identity is recorded as the job owner. The service validates world/source scope and selected-source revision before queueing, then validates it again when the worker executes.

The response is `202 Accepted` with `id`, `shreckworld_id`, `status`, `stage`, and timestamps. `GET /shreckworlds/{world_id}/agent-jobs/{job_id}` returns the same lifecycle fields, plus `result` when complete or a user-safe `error` when failed. Status is `queued`, `running`, `completed`, or `failed`; stages identify planning, retrieval, coverage assessment, targeted follow-up, reassessment, or completion. Only the job owner or an `admin` may poll it; an inaccessible or mismatched job returns `404`. Jobs are not cancellable. The existing synchronous query endpoint remains available and unchanged by job submission.

The `shreckworld_agent` Celery queue is consumed by the ShreckWorld worker alongside `shreckworld_embedding` with the worker's existing single-task concurrency bound. Jobs persist in `shreckworld_agent_jobs`; startup creates the table for existing installations. Queue-dispatch failures are marked failed and return `503`; execution failures are logged server-side and exposed as a generic retryable job error. The agent does not call Elder or Librarian runtimes.

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
- `502`: synthesis returned malformed structured output that could not be safely parsed. Well-formed but unverified generated claims are omitted and reported as partial/unsupported rather than failing the whole query.
- `422`: no complete passage fits the configured synthesis evidence budget.

Deploy this service API before enabling the reader frontend. The service's existing schema initialization creates the new model-settings table automatically; it requires no data backfill. Independent structured-ingestion/model changes may require reindexing; see the architecture/indexing guides. Rolling back the frontend or these reader routes preserves uploaded source files and management data. Unit tests use explicit model doubles and do not download model artifacts; real retrieval quality and mobile large-book performance require representative acceptance datasets/devices.

For ShreckLLM, configure `SHRECKWORLD_SHRECKLLM_BASE_URL`. The ShreckWorld
model target is managed through the settings API below; the legacy
`SHRECKWORLD_SYNTHESIS_PROVIDER` and `SHRECKWORLD_SYNTHESIS_MODEL` values only
provide its initial default before a target is saved.
`SHRECKWORLD_SYNTHESIS_TIMEOUT_SECONDS` controls the ShreckLLM job deadline
(default 45 seconds),
`SHRECKWORLD_SYNTHESIS_POLL_INTERVAL_SECONDS` controls status polling, and
`SHRECKWORLD_SYNTHESIS_EVIDENCE_TOKEN_BUDGET` caps complete evidence passages
supplied to synthesis (estimated at four characters per token). The optional
`SHRECKLLM_INTERNAL_SERVICE_TOKEN` is forwarded as a bearer token for
installations that protect internal ShreckLLM calls. Defaults are
`http://shreckllm:8110`, `openai`, `gpt-5-nano`, 45 seconds, 0.5 seconds, and
8,000 estimated evidence tokens respectively. Grounded answer synthesis allows
up to 4,000 output tokens. Synchronous SDK callers should configure
`Shrecknet(timeout=60)` or higher to allow for the ShreckWorld deadline and
network overhead; use the asynchronous agent-job endpoint when the client
cannot wait that long. Evidence mode and source reading do not require ShreckLLM availability.

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

## Character Companion delegated evidence

`POST /shreckworlds/{id}/companion-evidence` reuses evidence-only querying for the
unified Character Companion. `POST /shreckworlds/{id}/companion-validate` rechecks
book/source references before final publication. Both require a short-lived,
turn-bound bearer retrieval grant authorized against Shrecknet; normal browser
access tokens are not interchangeable with these grants. Scope limits the
ShreckWorld and optional library IDs. Configure the trusted Shrecknet origin and
keep these server-to-server requests on the internal network. Existing reader and
query routes remain unchanged. See [Character Companion](../Agents/Companion/Companion.md).
