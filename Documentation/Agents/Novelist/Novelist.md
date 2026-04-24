# Novelist Agent

This document describes the current Novelist implementation for chapter draft generation.

## Jobs Overview

Main endpoints:

- `POST /jobs/novelist/{agent_id}/runs`
- `POST /jobs/novelist/{agent_id}/runs/upload`
- `GET /jobs/novelist/runs/{run_id}`
- `GET /jobs/novelist/{agent_id}/runs`

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

### Steps 2-5 (Incremental `scene_packages`)

The same scene package is enriched incrementally:

- Step 2 adds exploration fields (`scene_goal`, `scene_tone`, `prior_knowledge_needed`)
- Step 3 adds retrieval/context fields (`prior_events`, `relationship_summaries`, `personality_reminders`, `unresolved_tensions`, `style_details`, `contradiction_warnings`, plus retrieval `queries` and `questions_answers`)
- Step 4 adds intent fields (`what_happens`, `emotional_progression`, `speaking_goals`, `implied_history`, `forbidden_contradictions`)
- Step 5 adds `prose_html`

Debug files for `step_2_response.json` to `step_5_response.json` now use strict shape:

```json
{ "scene_packages": [ ... ] }
```

No trace/token payloads are emitted in those primary step response files.

### Step 5 prose limiting

- Code-side paragraph clipping is removed.
- Novelist keeps full LLM HTML output after readability normalization.
- Paragraph/length control is prompt-driven at the LLM layer.

### Steps 6-7 editorial lane

- Steps 6 and 7 execute after all scene threads complete.
- They use a dedicated conversation lane (`step6_7`) isolated from scene-level chats.
- Step 6 (`critic`): no-memory call, input is only the concatenated draft text.
- Step 7 (`revision`): same dedicated conversation id with memory enabled, using step 6 critic feedback.

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

## Shared Component Reuse

Novelist intentionally reuses existing systems.

Architect reuse (scaffolding):

- `_run_scene_chunking_phase`
- `_run_entity_proposal_phase`
- `_run_scene_proposal_phase`
- `_run_milestone_proposal_phase`
- `_load_existing_nodes`
- `_format_ontology_definitions_from_entities`

Elder reuse (retrieval):

- Scene retrieval runs via Elder query integration and is normalized into scene context buckets.

## Related Docs

- Endpoints: [Endpoints/Novelist - Endpoints.md](Endpoints/Novelist%20-%20Endpoints.md)
- Pipeline details: [Generate_Draft/Generate_Draft.md](Generate_Draft/Generate_Draft.md)
