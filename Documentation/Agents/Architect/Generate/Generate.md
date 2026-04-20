# Architect Generate Job

This document describes the Architect generation job that persists validated proposals into the graph.

## Goal

The generate job takes frontend-reviewed Architect output and applies it to the ontology instance in a strict, auditable order:

1. Insert approved new entities.
2. Insert approved scenes (with scene ordering and strict related_to resolution).
3. Insert approved milestones (with milestone ordering and strict related_to resolution).
4. Enrich and update validated entities (all update-marked entities and all newly created entities).

After persistence, it triggers linking and embedding jobs for impacted entities/instances.

## Entry Point

API endpoint:

- POST /jobs/architect/runs/{run_id}/generate

Background task:

- architect.generate_entities

Task module:

- shrecknet/app/tasks/architect_generation.py

## Input Contract

Request model:

- ArchitectGenerationRequest

Top-level fields:

- run_id
- reviewed_pipeline_output
- author_type
- author_id

Critical validation:

- Path run_id must match payload run_id.

Generation reads proposal sections from:

- reviewed_pipeline_output.outputs.entity_proposals
- reviewed_pipeline_output.outputs.scene_proposals
- reviewed_pipeline_output.outputs.milestones_per_scene

## Status Handling Rules

Included for processing:

- approved
- approved_with_updates
- merged

Excluded from processing:

- disapproved

## Ordered Persistence Flow

### Step 1: New Entities

- Resolves corrected alias/definition updates when provided by frontend.
- Creates approved new entities first so downstream scene/milestone relation resolution can target newly created nodes.
- Tracks proposal-to-entity mapping for later proposal state synchronization.

### Step 2: Scenes

- Inserts approved scenes in scene_order sequence.
- Persists scene order links via preceded_by/followed_by semantics.
- Uses strict related_to resolution: every related_to entity must resolve to an existing or newly created entity id.
- Rejects unresolved aliases.

### Step 3: Milestones

- Inserts approved milestones grouped by scene_ref.
- Persists local milestone order via preceded_by_milestone_id.
- Applies strict related_to resolution and keeps links bounded to entities already related to the owning scene.

### Step 4: Isolated Entity Enrichment and Update

For each validated entity target (all update-marked entities and all newly created entities), generation runs an isolated enrichment pass using scene-bounded evidence:

Inputs used per entity:

- Existing entity summary/properties/relationships
- Scene texts for scenes where this entity is related in the current update
- Ontology property/relationship auto-generatable definitions
- Scene-bounded candidate target entities

Outputs applied in strict order:

1. Updated summary only when new evidence adds non-duplicate information.
2. Property updates only when evidence indicates a new or changed value.
3. Relationship additions only when strongly justified and target resolves inside the same scene context bounds.

## Post-Generation Jobs

If there are impacted entities, generation triggers:

1. link_instance
2. embed_nodes
3. embed_instance

This keeps linked text and embeddings aligned with newly persisted graph state.

## Proposal State Synchronization

After successful persistence:

- Proposal statuses are synced to approved/rejected.
- generated_entity_instance_id is stored for proposals that created entities.

## Failure Behavior

- Job is tracked via background_jobs lifecycle (running, progress checkpoints, done/failed).
- Hard failures mark the background job as failed and stop downstream actions.
- Strict unresolved relation cases fail fast to prevent partial inconsistent relation writes.

## Notes

- OpenAI configuration is required for enrichment.
- The generate job expects reviewed pipeline output (frontend-curated payload), not raw analyze output.
- Relationship resolution is intentionally strict to avoid unresolved placeholders.
