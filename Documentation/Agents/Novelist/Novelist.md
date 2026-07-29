# Novelist Agent

This document describes the current Novelist implementation for chapter draft generation.

Primary implementation:

- `shrecknet/app/jobs/novelist/novelist.py`
- `shrecknet/app/tasks/novelist.py`
- `shrecknet/app/api/routers/novelist.py`

## Model Configuration

Novelist uses dedicated model targets for grouped stages:

- `model_novelist_planning`: planning stages (step 2 + step 4).
- `model_novelist_prose`: per-scene prose generation (step 5).
- `model_novelist_critic`: critic stage (step 6).
- `model_novelist_chapter_writer`: exclusive final chapter rewrite model (step 7).

Compatibility/fallback behavior:

- `model_novelist_planning` falls back to `LLMTask.SYNTHESIS` policy target.
- `model_novelist_prose` falls back to `LLMTask.SYNTHESIS` policy target.
- `model_novelist_critic` falls back to `LLMTask.SYNTHESIS` policy target.
- `model_novelist_chapter_writer` falls back to `model_novelist_prose` when no
  dedicated target is attached to a legacy runtime policy.

## Jobs Overview

Main endpoints:

- `POST /jobs/novelist/{agent_id}/runs`
- `POST /jobs/novelist/{agent_id}/runs/upload`
- `GET /jobs/novelist/runs/{run_id}`
- `GET /jobs/novelist/{agent_id}/runs`

Upload route note:

- `POST /jobs/novelist/{agent_id}/runs/upload` accepts `.txt` or `.pdf`.
- PDF extraction normalizes lines into paragraph-like blocks before orchestration.

## Pipeline Runtime Stages

Internal stage progression:

1. `ingest`
2. `scaffolding`
3. `scene_package`
4. `retrieval`
5. `intent_drafting`
6. `prose_generation`
7. `critic`
8. `revision`
9. `merging`
10. `done`

## Current Functional Behavior (Step-by-Step)

### Step 1 (`scaffolding`)

- Produces base `scenes` list.
- Scene objects include base fields used for the incremental package flow:
  - `scene_id`, `name`, `scene_summary`, `source_rawtext`
  - `milestones`, `related_entities`
  - `instructions`, `Language_output_text`

### Steps 2-5 (Per-scene pipeline)

Current flow per scene:

- Step 2 plans retrieval questions (2-3) from scene title, description, and the
  complete raw scene excerpt. All scene calls are submitted together.
- Step 3 retrieves elder context and stores retrieval traces.
- Steps 4 and 5 form one scene-local bundle: Step 4 builds strict context JSON
  from complete raw evidence plus prior-knowledge Q/A, then Step 5 generates prose
  using the same isolated conversation and the raw evidence again.
- Independent Step 4→5 bundles run concurrently. A failed bundle is retried once
  with a fresh conversation id; a second failure fails the run.

ShreckLLM owns LLM queueing and provider concurrency. Novelist does not locally
throttle Stage 2, Elder question submission, or active Stage 4→5 bundles.

Per-step usage is tracked in:

- `artifacts.llm_usage_summary`
- `artifacts.llm_usage_by_step_novelist`

### Step 5 prose limiting

- Code-side paragraph clipping is removed.
- Novelist keeps full LLM HTML output after readability normalization.
- Paragraph/length control is prompt-driven at the LLM layer.
- Complete raw scene evidence is included without application-level clipping.

### Steps 6-7 editorial passes

- Steps 6 and 7 execute after all scene threads complete.
- Both calls are memory-free. Their conversation identifier is retained only for
  request correlation and does not load or persist ShreckLLM conversation history.
- Step 6 (`critic`): input is only the concatenated draft text.
- Step 7 (`revision`): uses `model_novelist_chapter_writer` and explicitly receives
  both the complete bounded draft and the normalized step 6 critic feedback.
- Step 7 requests an output allowance of up to `15,000` tokens.

Steps 2, 4, and 6 request strict native JSON-schema output. Providers that
explicitly reject structured output fall back to plain JSON plus the shared repair
model.

### Final HTML assembly

- Final merged HTML uses scene title headers as `<h1>{scene_name}</h1>` before scene prose blocks.

## Output Contracts

### Internal orchestrator result (V2)

`NovelistOrchestrator.execute(...)` returns:

```json
{
  "scene_packages": [ ... ],
  "critic_remarks": { ... },
  "final_text_html": "..."
}
```

### Persisted run model / API run reads

`NovelistRunRead` remains the API response model and includes persisted fields such as:

- `draft_text` (final HTML)
- `critic_notes` (JSON string)
- `artifacts` (stage artifacts and timings)
- derived fields like `timing_summary`, `scene_progress`, `step_outputs` when present in `artifacts`
- continuity fields: `previous_session_id`, `previous_session_summary`, `previous_session_lookup_status`

## Shared Component Reuse

Novelist intentionally reuses existing systems.

Architect reuse (scaffolding):

- `_run_scene_chunking_phase`
- `_run_scene_proposal_phase` (scene-only scaffolding path for Novelist)
- `_load_existing_nodes`
- `_format_ontology_definitions_from_entities`

Elder reuse (retrieval):

- Scene retrieval runs via Elder query integration and is normalized into scene context buckets.
- Elder calls are intentionally non-authoritative flavor/context support (fast context mode).

## Related Docs

- Endpoints: [Endpoints/Novelist - Endpoints.md](Endpoints/Novelist%20-%20Endpoints.md)
- Pipeline details: [Generate_Draft/Generate_Draft.md](Generate_Draft/Generate_Draft.md)
