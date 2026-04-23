# Novelist Generate Draft Pipeline

This document describes the generate-draft pipeline executed by Novelist runs.

Main implementation path:

- shrecknet/app/tasks/novelist.py
- shrecknet/app/jobs/novelist/novelist.py

## Goal

Given unstructured session narrative, produce a coherent revised chapter as HTML with full traceable scene-by-scene artifacts.

## Runtime Flow

### Stage 0: ingest

- Validates request payload and agent job type.
- Resolves optional previous session context.
- Builds continuity brief (compact context summary) used for consistency.

Artifacts:

- artifacts.inputs

### Stage 1: scaffolding (step_1)

Purpose:

- Produce scene scaffolding with scene list, milestones, and related entities.

Important:

- This stage reuses Architect shared plumbing directly.
- No separate Novelist-only extraction pipeline is intended.

Shared Architect functions used:

- _run_scene_chunking_phase
- _run_entity_proposal_phase
- _run_scene_proposal_phase
- _run_milestone_proposal_phase
- _load_existing_nodes
- _format_ontology_definitions_from_entities

Result shape:

- step_outputs.step_1.label = scene_scaffolding
- step_outputs.step_1.scenes[]

Each scene includes:

- scene_id
- name
- scene_summary
- milestones
- related_entities
- source_anchors
- new_or_update

### Stage 2: scene_package (step_2)

Purpose:

- Build complete per-scene writing package for downstream drafting.

Result shape:

- step_outputs.step_2.label = scene_writing_packages
- step_outputs.step_2.scene_packages[]

Each package includes:

- scene_id
- source_paragraphs
- raw_scene_text
- scene_summary
- scene_goal
- milestones
- related_entities
- temporal_position_hint
- tone_hint
- open_questions_for_retrieval

### Stage 3: retrieval (step_3)

Purpose:

- Retrieve scene-local narrative context via Elder agent stack.

Important:

- Novelist uses ElderOrchestrator and ElderQueryRequest directly.
- Retrieval output is normalized into context buckets per scene.

Result shape:

- step_outputs.step_3.label = scene_narrative_context
- step_outputs.step_3.narrative_context_by_scene[]

Each item includes:

- scene_id
- queries
- narrative_context

Narrative context buckets include:

- prior_events
- relationship_summaries
- personality_reminders
- unresolved_tensions
- style_details
- contradiction_warnings

### Stage 4: intent_drafting (step_4)

Purpose:

- Produce intended scene draft plan before prose generation.

Result shape:

- step_outputs.step_4.label = scene_intended_draft_output
- step_outputs.step_4.scene_intents[]

Each scene intent includes:

- scene_id
- what_happens
- emotional_progression
- speaking_goals
- implied_history
- forbidden_contradictions

### Stage 5: prose_generation (step_5)

Purpose:

- Generate prose per scene in HTML.

Result shape:

- step_outputs.step_5.label = scene_prose_output
- step_outputs.step_5.scene_prose[]

Each scene prose item includes:

- scene_id
- name
- scene_summary
- prose_html

### Stage 6: critic (step_6)

Purpose:

- Critique scene set for continuity, pacing, duplication, transitions, and contradictions.

Result shape:

- step_outputs.step_6.label = critic_response
- step_outputs.step_6.critic

### Stage 7: revision + merging (step_7)

Purpose:

- Apply revision output and merge scene results into final chapter text.

Result shape:

- step_outputs.step_7.label = full_rewritten_text
- step_outputs.step_7.final_rewritten_text
- step_outputs.step_7.revised_scenes
- step_outputs.step_7.lineage

Final text fields:

- Primary: step_outputs.step_7.final_rewritten_text
- Compatibility: draft_text

## Persisted Response Fields

Beyond step outputs, NovelistRunRead also includes:

- scene_results
- timing_summary
- stage_timings
- scene_progress
- critic_notes
- artifacts

## Progress Mapping (Job Tracking)

Progress markers are emitted for:

- scaffolding
- scene_package
- retrieval
- intent_drafting
- prose_generation
- critic
- revision
- merging

Each marker can include counts and timing summary details.

## Notes

- Pipeline fanout is bounded with max concurrency of 10 for scene-level parallel work.
- Artifacts are persisted to keep each transformation auditable.
