# Architect Entity Proposal

This document describes the current entity proposal phase in the Architect analysis job.

## Scope

Entity proposal runs after scene chunking output is finalized and before scene proposal/milestone assembly.

Current goals:

- Extract candidate entities with one queued LLM call per final scene.
- Let the LLM classify each extracted entity as `existing` or `new` using existing entity aliases and ontology names only.
- Keep graph ids out of the LLM prompt.
- Resolve `existing` matches back to internal node ids deterministically by alias and ontology after the LLM response.
- Deduplicate aliases across scenes using canonical and alias-equivalence logic.
- Classify each proposal as `updated` (matched existing entity) or `new`.
- Emit auditable artifact output (no graph writes in analysis).

## Inputs

- `run_id`
- `ontology_instance_id`
- Flattened scene inputs from chunking phase:
  - `scene_ref`
  - `scene_name`
- `scene_description`
- `scene_text`
- Ontology definitions (auto-generatable entity names and descriptions)
- Existing entity prompt catalogue:
  - `alias`
  - `ontology`

The internal existing-node catalogue still includes `node_id`, but `node_id` is never sent to the LLM.

## Prompt and Model

- Extraction and reconciliation prompt: `ARCHITECT_ENTITY_PROPOSAL_PROMPT`
- Model: canonical Architect analysis target
  `settings.model_architect_scene_chunking`
- Call unit: one scene per LLM call

LLM extraction parses to `SceneEntityBatchExtractionResponse` and expects:

- `scenes[]` with:
  - `scene_ref`
  - `entities[]`
- each entity includes:
  - `name`
  - `ontology`
  - `status` (`existing` or `new`)
  - `matched_alias` (exact existing alias when `status=existing`, otherwise null)
  - `confidence`
  - `why`

The entity extraction prompt does not ask for milestones, milestone links, mentions, or scene relationships. Milestones are generated later after scene proposals have `related_to` entities.

## Parallel Execution

- Architect creates all scene coroutines with `asyncio.gather(...)` so every
  request is submitted promptly to ShreckLLM.
- ShreckLLM owns queueing and enforces the configured global/provider concurrency.
- Each request contains the complete reconstructed `scene_text`; Architect does
  not apply a character limit.
- Scene-level failures produce empty entities for the affected scene and do not
  cancel other scenes.
- Calls request strict native JSON-schema output. Existing parse and repair logic
  remains as a fallback for unsupported or invalid structured output.

## Deduplication and Reconciliation

After batched scene-level extraction:

1. Ontology names are validated against allowed ontology definitions.
2. `existing` matches are resolved by matching `matched_alias` or `name` against the internal existing-node catalogue.
3. Invalid or unresolved `existing` matches are downgraded to `new`.
4. Aliases are canonicalized (case, spacing, parenthetical cleanup).
5. Dedup uses canonical keys with alias-equivalence matching across all scenes.
6. Proposals are finalized as:
   - `updated` when a valid existing node id was resolved after the LLM response
   - `new` when unmatched

Final proposal fields include:

- `proposal_type` (`update_instance` or `new_instance`)
- `entity_instance_id` (existing node id for updates)
- `proposal_metadata`:
  - `resolved_status` (`existing` or `new`)
  - `mention_count`
  - `chunk_indices`
  - `ontology_name`

Aggregate metrics include:

- `deduped_proposal_count`
- `updated_count`
- `new_count`
- `updated_by_ontology`
- `new_by_ontology`

## Logs and Timing

Key logs:

- `scene_entity_extraction_start`
- `scene_entity_extraction_scene_done`
- `scene_entity_extraction_batch_error`
- `scene_entity_extraction_total`
- `scene_entity_discovery_summary`

Timing field:

- `entity_discovery_elapsed_seconds`

## Artifacts

Per-run output directory:

- `local_tests/arhictect/Analyses/{run_id}/`

Entity artifact:

- `entity_proposal.json`

Contains:

- run and instance identity
- `scene_count`
- `entity_discovery_elapsed_seconds`
- `discovery_summary`
- `proposed_entities`

## Runtime Notes

- This phase is active in `architect.analyze_instance`.
- Analysis does not write entities to the graph.
- Milestone extraction runs after this phase and can only relate milestones to entities already present in the scene.
- Frontend reads consolidated output from `background_jobs.details.pipeline_output` after job status is `done`.
