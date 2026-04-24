# Novelist Generate Draft Pipeline

This document reflects the current pipeline implementation in:

- `shrecknet/app/tasks/novelist.py`
- `shrecknet/app/jobs/novelist/novelist.py`

## Goal

Transform unstructured narrative/session text into:

1. incrementally enriched per-scene packages,
2. global editorial critic remarks,
3. final rewritten chapter HTML.

## Runtime Flow

### Stage 0: ingest

- Validate payload and agent job type.
- Resolve optional previous session context.
- Prepare continuity support metadata in artifacts input fields.

### Stage 1: scaffolding (`step_1`)

- Build normalized scene list (Architect-backed scaffolding path).
- Output baseline scene structures used for all downstream steps.

### Stage 2: scene exploration (`step_2`)

- Build initial scene writing package.
- Adds exploration fields (tone, goal, prior knowledge questions/answers).

### Stage 3: retrieval context (`step_3`)

- Retrieve scene-specific evidence/context via Elder integration.
- Adds narrative context fields + retrieval query/answer traces into scene package fields.

### Stage 4: intent drafting (`step_4`)

- Adds intent fields to each scene package:
  - `what_happens`
  - `emotional_progression`
  - `speaking_goals`
  - `implied_history`
  - `forbidden_contradictions`

### Stage 5: prose generation (`step_5`)

- Generates scene prose HTML and adds `prose_html` into each scene package.
- Code no longer clips/limits paragraphs; full LLM response is retained after HTML normalization.

### Stage 6: critic (`step_6`)

- Runs after all scene threads complete.
- Uses a dedicated step6_7 conversation lane.
- Step 6 call is no-memory.
- Input is only the concatenated full draft text.
- Output JSON includes:
  - `global_notes`
  - `by_scene` keyed by scene title.

### Stage 7: revision (`step_7`)

- Runs in same dedicated step6_7 conversation id, with memory enabled.
- Input includes draft text + step 6 critic summary.
- Returns full revised HTML.

### Merging

- Final HTML is assembled with scene-title separation using:
  - `<h1>{scene_name}</h1>` + scene prose blocks.

## Debug Files

Step debug prompt/response files are written per run.

Current response shapes:

- `step_1_response.json`: scenes payload.
- `step_2_response.json`: `{ "scene_packages": [...] }`
- `step_3_response.json`: `{ "scene_packages": [...] }`
- `step_4_response.json`: `{ "scene_packages": [...] }`
- `step_5_response.json`: `{ "scene_packages": [...] }`

For steps 2-5, debug response files are intentionally scene-packages-only (no token summaries, no scene traces).

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
