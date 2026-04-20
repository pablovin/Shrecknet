# Architect Entity Proposal

This document describes the current entity proposal phase in the Architect analysis job.

## Scope

Entity proposal runs after scene chunking and before scene/milestone proposal.

Current goals:

- Extract candidate entities from each detected scene.
- Deduplicate aliases across scenes using canonical and alias-equivalence logic.
- Reconcile deduplicated entities against existing graph candidates.
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
- Existing node catalogue loaded through retrieval (`node_id`, `alias`, `ontology`)

## Prompt and Model

- Extraction prompt: `ARCHITECT_ENTITY_PROPOSAL_PROMPT`
- Reconciliation prompt: `ARCHITECT_ENTITY_RECONCILATION_PROMPT`
- Model: `LLMTask.ARCHITECT_EXTRACT` selected by `ModelPolicy`

LLM extraction parses to `ChunkExtractionResponse` and expects `entities[]` with:

- `name`
- `ontology`
- `confidence`
- `why`

## Parallel Execution

- Concurrency cap: `10`
- Mechanism: `asyncio.Semaphore(10)` plus `asyncio.gather(...)`
- Failure isolation: scene-level failures produce empty entities for that scene and continue

## Deduplication and Reconciliation

After scene-level extraction:

1. Aliases are canonicalized (case, spacing, parenthetical cleanup).
2. Dedup uses canonical keys with alias-equivalence matching.
3. Existing catalogue is prefiltered using:
   - exact canonical match
   - alias-equivalence match
   - token overlap threshold (`MIN_TOKEN_OVERLAP_RATIO = 0.5`)
4. Reconciliation is run against filtered candidates.
5. Proposals are finalized as:
   - `updated` when matched to existing node
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
- `scene_entity_extraction_scene_error`
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
- Frontend reads consolidated output from `background_jobs.details.pipeline_output` after job status is `done`.
