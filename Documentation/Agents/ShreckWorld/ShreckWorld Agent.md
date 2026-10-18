# ShreckWorld Agent

## Purpose and ownership

The ShreckWorld Agent is an independent, evidence-first query job owned by the
`shreckworld/` service. It is not an Elder mode or a Librarian extension and does
not import either runtime. It reuses ShreckWorld's existing indexed sources,
structured document generations, hybrid retrieval, citations, and ShreckLLM
service integration. This milestone does not generate a user-facing synthesized
answer.

## Pipeline

1. **Authorize and resolve scope.** The API authenticates the Shrecknet user,
   validates the requested ShreckWorld and every selected source, and checks a
   selected passage's source revision. The worker repeats these checks before
   execution. The planner cannot choose or broaden source scope.
2. **Frame needs.** Simple questions take a deterministic one-need path with no
   planner call. Questions classified as complex may use one structured
   ShreckLLM call over the question, world name, source titles/authors, and
   optional selected passage. The strict `QueryPlan` has 1–6 ordered unique
   needs, bounded kinds and priorities, and no source IDs or execution controls.
   Invalid or unavailable planning falls back to a deterministic one-need plan
   and is marked in `v3_trace.planner_fallback`.
3. **Retrieve candidate evidence.** For each need, ShreckWorld searches the same
   already-resolved active document and embedding-fingerprint scope using E5,
   Qdrant, FTS5, exact terms, RRF, and the existing reranker. Searches run
   sequentially against the request's SQLAlchemy session; sessions are not
   shared between threads.
4. **Preserve and hydrate evidence.** The top distinct parents per need are
   merged by `(document_id, parent_chunk_id)`. Different documents/books remain
   distinct, and each output evidence group retains all associated need IDs.
   Selected complete logical units are hydrated with ShreckWorld citation and
   page provenance. The approximate character-based evidence budget omits an
   oversized logical unit whole and records the omission; it never truncates a
   rule or table into an unmarked partial quote. Included group IDs equal their
   citation's `source-N`; omitted groups use `omitted-{chunk_id}`, contain no
   citation, and include an omission reason.
5. **Assess coverage.** A single batched ShreckLLM reviewer call can assess all
   needs together. Every accepted support claim must cite an output `source-N`
   ID and an exact quote occurring in that citation. Invalid output, missing
   quotes, and unavailable verification are `unverified`/`unresolved`, never
   covered by retrieval score alone. If requested sources are unindexed or
   incompatible, overall coverage cannot be `adequate`; `source_coverage` shows
   how many sources were searched and why others were not.
6. **Targeted recovery.** At most one additional round searches needs still
   missing, partial, or unresolved, using only their question and declared
   coverage requirement. Refinement terms are retained only when present in the
   original question, selected passage, requested source metadata, or initial
   retrieved candidates; the original question always remains in the search.
   Existing citations are retained; newly retrieved evidence shares the
   remaining budget. Coverage may be reassessed once after that round.
   Exhaustive requests or unproven interactions are not treated as complete
   because no counterexample was retrieved.
7. **Return evidence.** The synchronous route and persisted job return the same
   planned evidence response, including the validated plan, per-need coverage,
   citations, source coverage, and optional V3 trace. No answer-generation call
   occurs. Existing `mode="answer"` is available only with `single` retrieval.

## Job lifecycle and persistence

`POST /shreckworlds/{world_id}/agent-jobs` stores the request, authenticated
owner, world, status, stage, result, and safe failure detail in
`shreckworld_agent_jobs`, then queues `shreckworld.agent.query` on the
`shreckworld_agent` queue. The ShreckWorld worker consumes that queue alongside
`shreckworld_embedding` with its existing single-task concurrency limit.
`GET /shreckworlds/{world_id}/agent-jobs/{job_id}` is restricted to the owner or
an admin; other users receive `404`. Job status is `queued`, `running`,
`completed`, or `failed`. Stages report planning, retrieval, coverage review,
targeted recovery, reassessment, completion, or failure. There is no cancellation
operation.

The existing startup `create_schema()` call creates the new table on deployed
SQLite installations; no data backfill is required. World deletion cascades job
rows. A queue-dispatch failure persists a failed job and returns `503`; execution
failures are logged and return a generic retry instruction to the owner. Rolling
back the service does not remove prior query jobs or alter indexed source data.

## Configuration

- `model_shreckworld_synthesizer` is the shared ShreckLLM provider/model target
  for planner, evidence-coverage reviewer, and grounded answer calls. Admins and
  world builders read and update it through
  `GET`/`PUT /admin/settings/model-targets/shreckworld_synthesizer`. It is
  persisted in the ShreckWorld database and applies when a worker starts an
  agent job.
- Until a target is saved, `SHRECKWORLD_SYNTHESIS_PROVIDER` and
  `SHRECKWORLD_SYNTHESIS_MODEL` supply the initial value for existing
  deployments. The persisted target takes precedence afterward.
- `SHRECKWORLD_QUERY_V3_PLANNING_ENABLED` defaults to `true`; it controls the
  optional complex-question planner call.
- `SHRECKWORLD_QUERY_V3_COVERAGE_REVIEW_ENABLED` defaults to `true`; when false,
  coverage is diagnostic and remains unverified.
- `SHRECKWORLD_QUERY_V3_EVIDENCE_TOKEN_BUDGET` defaults to `8000` and is bounded
  from 1,000 to 32,000. It is estimated at four characters per token, not a
  tokenizer guarantee.
- Planner and reviewer calls use the configured ShreckLLM base URL, shared
  ShreckWorld model target, timeout, poll interval, and optional internal service token.
  They do not invoke answer synthesis.

## API and SDK

See the [reader API contract](../../API/SHRECKWORLD_READER_API.md) for request
fields, planned response models, job authorization, errors, and compatibility.
Python clients can use `ShreckWorldAPI.query(..., retrieval_strategy="planned")`
for an opt-in synchronous response, or `start_agent_query()` and `agent_job()`
for the persisted lifecycle. See the [SDK reader workflow](../../../python_sdk/docs/core_workflows/shreckworld-reader.md).
