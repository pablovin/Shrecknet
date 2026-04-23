# Novelist Agent

This document describes Novelist in production scope.

## Jobs Overview

Novelist is a scene-centric chapter generation pipeline that transforms unstructured session text into a revised full chapter output.

Main run endpoint:

- POST /jobs/novelist/{agent_id}/runs

Upload run endpoint:

- POST /jobs/novelist/{agent_id}/runs/upload

Run read endpoints:

- GET /jobs/novelist/runs/{run_id}
- GET /jobs/novelist/{agent_id}/runs

## Pipeline Runtime Stages

Internal stage progression:

1. ingest
2. scaffolding
3. scene_package
4. retrieval
5. intent_drafting
6. prose_generation
7. critic
8. revision
9. merging
10. done

## Shared Component Reuse

Novelist intentionally reuses existing systems instead of duplicating extraction/retrieval logic.

Architect reuse for step-1 scaffolding:

- _run_scene_chunking_phase
- _run_entity_proposal_phase
- _run_scene_proposal_phase
- _run_milestone_proposal_phase
- _load_existing_nodes
- _format_ontology_definitions_from_entities

These are shared from:

- shrecknet/app/tasks/architect_analysis.py

Elder reuse for retrieval:

- Novelist retrieval executes through ElderOrchestrator with ElderQueryRequest.
- Returned Elder sources are transformed into scene-local narrative context buckets for prose drafting.

## Frontend Step Outputs

NovelistRunRead exposes sectioned output under step_outputs:

- step_1: scene_scaffolding
- step_2: scene_writing_packages
- step_3: scene_narrative_context
- step_4: scene_intended_draft_output
- step_5: scene_prose_output
- step_6: critic_response
- step_7: full_rewritten_text

Final rewritten text location:

- step_outputs.step_7.final_rewritten_text

Compatibility field:

- draft_text (same final merged chapter text)

## Timing and Progress Fields

Run payload timing/progress fields:

- timing_summary
- stage_timings
- scene_progress

These are intended for progress bars, observability, and latency diagnostics.

## Related Docs

- Endpoints: [Novelist - Endpoints.md](Endpoints/Novelist%20-%20Endpoints.md)
- Generate draft pipeline details: [Generate_Draft.md](Generate_Draft/Generate_Draft.md)
