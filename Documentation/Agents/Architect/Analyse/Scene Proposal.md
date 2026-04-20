# Architect Scene Proposal

This document describes the current scene proposal phase in the Architect analysis pipeline.

## Purpose

Scene proposal links ordered scene outputs with deduplicated entity proposals and adds scene-to-scene navigation metadata.

## Inputs

- Scene inputs from chunking phase (`scene_ref`, `chunk_index`, `scene_name`, `scene_description`, `scene_text`, source entity metadata)
- Entity proposals from entity phase (`scene_refs`, canonical and alias fields, proposal status metadata)
- `author_id`

## Process

1. Build a scene-to-entity index from entity proposals (`scene_refs`).
2. For each scene, create a proposal object with stable references and provenance.
3. Populate `related_to` with entity references that appear in that scene.
4. Add sequence links:
   - `preceded_by`
   - `followed_by`

## Scene Output Shape

Each proposed scene includes:

- `scene_order`
- `scene_ref`
- `scene_id`
- `chunk_index`
- `source_entity_instance_id`
- `source_entity_alias`
- `scene_index`
- `scene_name`
- `scene_description`
- `scene_text`
- `related_to` (list of objects)
  - `proposal_index`
  - `canonical`
  - `alias`
  - `status`
  - `proposal_type`
  - `entity_instance_id`
- `author`
  - `created_by_type`
  - `created_by_author`
- `derived_from`
  - `entity_instance_id`
- `preceded_by`
- `followed_by`

## Timing and Logs

- Phase timing field: `scene_proposal_elapsed_seconds`
- Logs:
  - `scene_proposal_start`
  - `scene_proposal_total`

## Artifact

Output artifact:

- `proposed_scenes.json`

Stored under:

- `local_tests/arhictect/Analyses/{run_id}/`

Payload fields:

- `run_id`
- `agent_id`
- `ontology_instance_id`
- `scene_count`
- `scene_proposal_elapsed_seconds`
- `proposed_scenes`

## Notes

- This phase is deterministic and in-memory (no graph writes).
- Scenes may be pruned later by milestone phase if they produce zero milestones.

## See Also

- [Scene Chunking.md](Scene%20Chunking.md)
- [Entity Proposal.md](Entity%20Proposal.md)
- [Milestones Proposal.md](Milestones%20Proposal.md)
- [Architect.md](Architect.md)
