# Architect Milestones Proposal

This document describes how milestone proposals are generated in the active Architect analysis pipeline.

## Purpose

Milestones are generated after final scene proposals have been built and linked to scene-local entities.

Current goals:

- Generate graph-worthy milestones from final scenes, not from segmentation.
- Use scene-local `related_to` entities as the only allowed entity set for milestone mentions and relationships.
- Keep milestone descriptions concise, concrete, and entity-aware.
- Preserve the public output shape for frontend validation and later persistence.
- Attach provenance fields (`author`, `derived_from`) for auditing.

## Inputs

- `run_id`
- Final scene proposals:
  - `scene_ref`
  - `scene_id`
  - `scene_name`
  - `description`
  - `source_chunk_index`
  - `start_paragraph`
  - `end_paragraph`
  - `related_to`
- Compressed scene text for evidence
- Scene-local allowed entities from `scene.related_to`
- Author metadata (`created_by_type`, `created_by_author`)

## Active Runtime Behavior

In the active analysis path:

1. Scene segmentation runs first and does not produce milestones.
2. Scene metadata is deduplicated/merged.
3. Entity discovery runs per final scene.
4. Scene proposals are built with `related_to` entities.
5. `_run_milestone_proposal_phase` sends final scenes to `ARCHITECT_MILESTONE_BATCH_PROMPT` in batches of at most 5 scenes.
6. Returned milestones are mapped back by `scene_ref`.
7. Boundaries are normalized so each retained scene has at least one `begin` and one `end` milestone.

The milestone prompt receives only final scene payloads and allowed scene entities. It must not introduce entities outside the scene-local allowed entity list.

## Prompt Contract

`ARCHITECT_MILESTONE_BATCH_PROMPT` returns:

- `scenes[]`
  - `scene_ref`
  - `milestones[]`
    - `title`
    - `description`
    - `boundary_type` (`begin|end|none`)
    - `mentions`
    - `adjacent_to`
    - `related_to`
      - `entity`
      - `relationship_label`
      - `relationship_description`

Prompt constraints:

- Group milestones by `scene_ref`.
- Return at least one `begin` and one `end` milestone per scene when supported.
- Keep normal scenes to 2-4 milestones; never exceed 6.
- Keep milestone descriptions to a maximum of 2 sentences.
- Mention involved entities by name when supported by the text.
- Use only allowed scene entities in `mentions` and `related_to`.

## Output Shape

Each milestone in analysis output includes:

- `milestone_ref`
- `scene_ref`
- `scene_id`
- `title`
- `label` (kept for backward compatibility)
- `description`
- `boundary_type` (`begin|end|none`)
- `mentions`
- `adjacent_to`
- `related_to`
  - `entity`
  - `relationship_label`
  - `relationship_description`
- `milestone_order`
- `author`
  - `created_by_type`
  - `created_by_author`
- `derived_from`
  - `entity_instance_id`

## Boundary and Pruning Rules

- If milestones exist for a scene, boundaries are normalized to ensure at least one `begin` and one `end`.
- If a scene has zero milestones, default begin/end placeholders are injected.
- Adjacent duplicated boundary milestones (`end` -> `begin`) are deduplicated when signatures match.
- Milestone `related_to` entries that reference entities outside the scene-local entity set are dropped.

## Artifact

Output artifact:

- `proposed_milestones.json`

Stored under:

- `local_tests/arhictect/Analyses/{run_id}/`

Artifact fields include:

- `run_id`
- `agent_id`
- `ontology_instance_id`
- `scene_count`
- `milestone_count`
- `removed_scene_count`
- `removed_scene_refs`
- `milestone_proposal_elapsed_seconds`
- `milestones_per_scene`
- `proposed_milestones`

## Observability

Runtime logs include:

- `milestone_proposal_start`
- `milestone_proposal_batch_done`
- `milestone_boundary_dedup_applied`

## Downstream Use

This phase is analysis-only and does not write milestones to the graph. Results are used for:

- Human validation and curation
- UI proposal display
- Later generation/persistence flow

## See Also

- [Scene Chunking.md](Scene%20Chunking.md)
- [Entity Proposal.md](Entity%20Proposal.md)
- [Scene Proposal.md](Scene%20Proposal.md)
