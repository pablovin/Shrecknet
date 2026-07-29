# Novelist Generate Draft Pipeline

This document reflects the current pipeline implementation in:

- `shrecknet/app/tasks/novelist.py`
- `shrecknet/app/jobs/novelist/novelist.py`

## Goal

Transform unstructured narrative/session text into:

1. Architect-scaffolded scene packages,
2. per-scene retrieval/context + prose drafts,
3. global critic remarks,
4. final rewritten chapter HTML.

## Runtime Flow

### Stage 0: ingest

- Validate payload and agent job type.
- Resolve optional previous session context.
- Prepare continuity support metadata in artifacts input fields.

### Stage 1: scaffolding (`step_1`)

- Build normalized scene list (Architect-backed scaffolding path).
- In the current flow, this is an Architect scene passthrough for Novelist (no extra Novelist merge prompt).

### Stage 2: retrieval question planning (`step_2`)

- Generate 2-3 retrieval questions per scene from scene title, description, and
  complete raw scene text.
- Submit every scene planning call together; ShreckLLM owns provider concurrency.
- This stage prepares retrieval intents only.

### Stage 3: retrieval context (`step_3`)

- Retrieve scene-specific prior context via Elder integration.
- Submit all scene-question lookups together without local LLM concurrency gates.
- Output includes retrieval query/answer traces used to build prior knowledge.

### Stage 4: context build (`step_4`)

- Build compact context JSON per scene from:
  - `scene_name`
  - `scene_description`
  - complete `source_rawtext`
  - `prior_knowledge` (`{question: answer}`)
- Output fields:
  - `prior_events`
  - `relationship_summaries`
  - `personality_reminders`
  - `unresolved_tensions`
  - `style_details`
  - `contradiction_warnings`

### Stage 5: prose generation (`step_5`)

- Generates per-scene prose HTML.
- Uses conversation memory from step 4 for each scene thread.
- Receives complete raw scene text explicitly.
- Steps 4 and 5 execute sequentially inside each scene bundle, while all scene
  bundles run concurrently.
- A failed bundle retries once with a fresh conversation id and then fails the run.

### Stage 6: critic (`step_6`)

- Runs after all scene threads complete.
- Does not load or persist ShreckLLM conversation memory.
- Retains an editorial conversation identifier only for request correlation.
- Input is only the concatenated full draft text.
- Uses strict native JSON-schema output and normalizes it to:
  - `global_notes`
  - `by_scene` keyed by scene title.

### Stage 7: revision (`step_7`)

- Uses the dedicated `model_novelist_chapter_writer` target.
- Does not load Stage 6 conversation history or persist conversation memory.
- Explicit input includes the complete bounded draft text and normalized Step 6
  critic summary, so the chapter writer has no implicit dependency on prior turns.
- Returns full revised HTML.
- Requests up to `15,000` output tokens.

### Merging

- Final chapter HTML is assembled in scene order with:
  - `<h1>{scene_name}</h1>` + scene prose blocks.

## Debug Files

Step debug prompt/response files are written per run.

Current response shapes:

- `step_1_response.json`: scenes payload.
- `step_2_response.json`: retrieval planning artifacts
- `step_3_response.json`: retrieval artifacts
- `step_4_response.json`: context build artifacts
- `step_5_response.json`: prose generation artifacts

LLM usage for Novelist is aggregated in run artifacts (`llm_usage_summary` and `llm_usage_by_step_novelist`).

## Output Contracts

### Internal orchestrator contract (V2)

`NovelistOrchestrator.execute(...)` returns:

```json
{
  "scene_packages": [ ... ],
  "critic_remarks": { ... },
  "final_text_html": "..."
}
```

### Persisted run fields

Celery/task layer persists:

- `draft_text` from `final_text_html`
- `critic_notes` from `critic_remarks` (JSON string)

The run record still exposes `artifacts` and derived fields through `NovelistRunRead`.

## Progress Mapping

Job progress updates are emitted for:

- `scaffolding`
- `scene_package`
- `retrieval`
- `intent_drafting`
- `prose_generation`
- `critic`
- `revision`
- `merging`

## Key Notes

- Scene fanout concurrency is bounded.
- Steps 6-7 are serialized after scene fanout completion.
- Scene package order is preserved through the pipeline.
