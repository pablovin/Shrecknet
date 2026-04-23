# Architect Milestones Proposal

This document describes the current milestone proposal phase in the Architect analysis pipeline.

## Purpose

The milestone phase expands proposed scenes into temporal checkpoints for review.

Current goals:

- Propose milestone candidates per scene with boundary role (`begin`, `end`, `none`).
- Keep milestones focused on important narrative events.
- Attach provenance fields (`author`, `derived_from`) for auditing.
- Attach scene-local entity relationships in milestone `related_to`.
- Emit artifact output for downstream validation.

## Inputs

- `run_id`
- Proposed scenes from scene proposal phase
- Scene-level entities already linked in `scene.related_to`
- Author metadata (`created_by_type`, `created_by_author`)

Per-scene prompt context includes:

- `scene_ref`
- `scene_name`
- `scene_description`
- `scene_text`
- Scene entity aliases

## Prompt and Model

- Prompt: `ARECHITECT_MILESTONE_PROPOSAL_PROMPT`
- Model: `LLMTask.ARCHITECT_EXTRACT` via `ModelPolicy`

## Parallel Execution

- Max concurrency: `10`
- Mechanism: `asyncio.Semaphore(10)` plus `asyncio.gather(...)`
- Failure strategy: scene-level isolation (failed scene returns empty milestone list)

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
- If a scene has zero milestones, that scene is pruned from final proposed scenes.
- Pruned scene refs are tracked in output (`removed_scene_refs`).

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
- `milestone_proposal_scene_total`
- `milestone_proposal_scene_prune`
- `milestone_proposal_total`

## Downstream Use

This phase is analysis-only and does not write milestones to the graph. Results are used for:

- Human validation and curation
- UI proposal display
- Later generation/persistence flow

## See Also

- [Scene Chunking.md](Scene%20Chunking.md)
- [Entity Proposal.md](Entity%20Proposal.md)
- [Scene Proposal.md](Scene%20Proposal.md)
- [Architect.md](Architect.md)
