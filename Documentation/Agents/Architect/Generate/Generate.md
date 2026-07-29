# Architect Generate Job

This document describes the Architect generation job that persists validated proposals into the graph.

## Character embodiment scope

When `embody_agents` is enabled, the generate job embodies CharacterAgents related
to scenes created by that job. The embodiment source group is the `DERIVED_FROM`
entity used by the generation bundle, and the scene input contains only scene IDs
created during the current generate job. Historical scenes associated with the
same CharacterAgent or `DERIVED_FROM` entity are not reinterpreted.

## Goal

The generate job takes frontend-reviewed Architect output and applies it to the ontology instance in a strict, auditable order:

0. Canonicalize all reviewed payload updates (entities, scenes, milestones).
1. Insert approved new entities.
2. Insert approved scenes (with scene ordering and strict related_to resolution).
3. Insert approved milestones (with milestone ordering and strict related_to resolution).
4. Enrich and update validated entities (all update-marked entities and all newly created entities).

After persistence, it triggers linking and a single coalesced embedding reconciliation job.

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
- reviewed_pipeline_output.outputs.scene_proposals or reviewed_pipeline_output.outputs.scenes
- reviewed_pipeline_output.outputs.milestones_per_scene or reviewed_pipeline_output.outputs.milestone_proposals or reviewed_pipeline_output.outputs.milestones

## Status Handling Rules

Included for processing:

- approved
- approved_with_updates
- merged

Excluded from processing:

- disapproved

## Step 0 Canonicalization (Critical)

Before any writes, generation computes canonical effective fields from each proposal and its updates payload.

For entities, canonicalization resolves:

- effective_name
- effective_proposal_type
- effective_entity_instance_id
- effective_definition_id
- effective_scene_refs
- effective_merge

For scenes, canonicalization resolves:

- effective_name
- effective_related_to

For milestones, canonicalization resolves:

- effective_name
- effective_related_to

### Supported update styles

Entity updates supported:

- name change
- entity type change (entity_definition_id)
- proposal type change (new_instance/update_instance)
- update_instance target entity change (entity_instance_id)
- merge object

Scene and milestone updates supported:

- related_to replacement via updates.related_to
- relationship_deletions for related_to
- scene additional_related_entity_instance_ids

### Merge target resolution

Merge target resolution is deterministic and uses this priority:

1. merge.maintained_alias (preferred)
2. merge.maintained_entity_instance_id / merge.maintained_entity_id
3. maintained/merged proposal index or proposal id references

This guarantees stable merge behavior when aliases are curated in frontend.

## Ordered Persistence Flow

### Step 1: New Entities

- Consumes only canonicalized entity fields from Step 0.
- Creates approved new entities first so downstream scene/milestone relation resolution can target newly created nodes.
- Tracks proposal-to-entity mapping for later proposal state synchronization.
- Skips disapproved proposals.

### Step 2: Scenes

- Consumes only canonicalized scene fields from Step 0.
- Inserts approved scenes in scene_order sequence.
- Persists scene order links via preceded_by/followed_by semantics.
- Persists Scene -> Entity RELATES_TO edges explicitly (new guarantee).
- Uses strict related_to resolution: every related_to entity must resolve to an existing or newly created entity id.
- Rejects unresolved aliases.
- Skips disapproved scenes.
- Verifies relation write counts (expected vs persisted).

### Step 3: Milestones

- Consumes only canonicalized milestone fields from Step 0.
- Inserts approved milestones grouped by scene_ref.
- Persists local milestone order via preceded_by_milestone_id.
- Applies strict related_to resolution and keeps links bounded to entities already related to the owning scene.
- Skips disapproved milestones.
- Verifies relation write counts (expected vs persisted).

### Step 4: Isolated Entity Enrichment and Update

Step 4 is the final write pass before post-generation jobs. It enriches every entity in `impacted_entity_ids` using evidence bounded strictly to the scenes where that entity appears.

#### Target set

`impacted_entity_ids` is the union of:

- `created_entity_ids` — entities inserted in Step 1 from `new_instance` proposals.
- `update_targets` — entities resolved in Step 0/1 from `update_instance` proposals.
- `merge_maintained_entity_ids` — entities kept as the maintained side of a merge.
- Entities related to approved scenes/milestones (added during Steps 2 and 3).

Scenes and milestones are **never** included in this set and are **never** written by Step 4.

## Post-Generation Reconciliation

After Step 4, generate performs one batched embedding trigger for all newly written/updated graph memory nodes from this run:

- Impacted entities (`EntityInstance.entity_instance_id`)
- Created scenes (`Scene.id`)
- Created milestones (`Milestone.id`)

The task sends this combined ID list to `ontology.embed_reconciliation` as `node_ids`, so embedding runs as a single coalesced step instead of separate entity/scenes/milestones fanout jobs.

#### Parallel LLM execution

LLM extraction (`_extract_properties_and_relationships`) runs in parallel across all target entities using runtime-configured architect concurrency (`resolve_effective_architect_concurrency`), with fallback default `_ENRICHMENT_CONCURRENCY = 10`. Graph writes are applied sequentially after all extractions complete to keep the Neo4j session single-threaded.

#### Inputs used per entity

- Existing entity `autogenerated_text`, `properties`, and `relationships` read from graph.
- `scene_text` from every scene where this entity appears (`entity_scene_refs` + `proposal_scene_refs` union).
- Ontology property and relationship definitions from `entity_definitions_map`.
- `allowed_targets` — the set of entity IDs related to those same scenes (from `scene_ref_to_entities`).

#### Outputs applied in strict order

1. **Summary update** — only when the new candidate summary contains information not already present in the stored summary (`_summary_contains` guard). Merged using `_merge_summaries`.
2. **Property updates** — only when the extracted value differs from or extends the currently stored value.
3. **Relationship additions** — only when:
	- The relationship definition exists in the entity's ontology definition.
	- The resolved `target_id` is in `allowed_targets` (scene-bounded entity-to-entity policy).
	- No duplicate edge with the same `relationship_definition_id` already exists between source and target.

#### Relationship guarantee

All relationships written in Step 4 are strictly `EntityInstance → EntityInstance` via `[:RELATES_TO]`. No edges to `Scene` or `Milestone` nodes are created. The `allowed_targets` set is built exclusively from `scene_ref_to_entities` which contains only resolved `entity_instance_id` values.

## Frontend Contract: updates Field

This section is the canonical contract frontend should follow when producing reviewed_pipeline_output.

### Where updates can appear

- entity_proposals[i].updates
- scenes[i].updates (or scene_proposals[i].updates)
- milestones[j].updates (inside milestones_per_scene, milestone_proposals, or milestones)

### Entity updates schema (practical)

Use any subset of these keys:

- name
- proposal_type
- entity_instance_id
- entity_definition_id
- ontology
- corrected_proposal_type
- corrected_entity_instance_id
- corrected_entity_definition_id
- merge

Example entity proposal with updates:

```json
{
	"name": "Rome",
	"status": "approved_with_updates",
	"proposal_type": "update_instance",
	"entity_instance_id": "08eed43b-5994-46fb-be97-147af4c11dcc",
	"ontology": "Important Locations",
	"scene_refs": ["chunk_0_scene_1"],
	"updates": {
		"name": "Romess",
		"entity_instance_id": "abf8ebda-03f4-4a8b-a54e-dbf9c4e51b4e",
		"entity_definition_id": 17,
		"proposal_type": "update_instance"
	}
}
```

Example merge payload:

```json
{
	"updates": {
		"merge": {
			"maintained_alias": "Hadrian's Wall",
			"maintained_entity_instance_id": "af274ee3-0e7b-4aa8-8504-ba7d390575b4",
			"maintained_entity_definition_id": 18,
			"merged_from_alias": "Britain",
			"merged_into_alias": "Hadrian's Wall"
		}
	}
}
```

### Scene updates schema (practical)

Use any subset of these keys:

- name
- related_to
- relationship_deletions
- additional_related_entity_instance_ids

Example scene updates payload:

```json
{
	"scene_ref": "chunk_0_scene_1",
	"status": "approved_with_updates",
	"scene_name": "Britain after Rome",
	"related_to": [
		{
			"proposal_index": 1,
			"alias": "Rome",
			"canonical": "rome",
			"proposal_type": "update_instance",
			"entity_instance_id": "08eed43b-5994-46fb-be97-147af4c11dcc"
		}
	],
	"updates": {
		"name": "Britain after Rome (Revised)",
		"relationship_deletions": [
			{
				"operation": "delete",
				"relation_type": "related_to",
				"target_alias": "Rome"
			}
		],
		"additional_related_entity_instance_ids": [
			"af274ee3-0e7b-4aa8-8504-ba7d390575b4"
		]
	}
}
```

### Milestone updates schema (practical)

Use any subset of these keys:

- related_to
- relationship_deletions

Example milestone updates payload:

```json
{
	"milestone_ref": "milestone-6c3474e7-5a04-4f7a-9d86-8ea989b66c36",
	"scene_ref": "chunk_0_scene_1",
	"status": "approved_with_updates",
	"title": "Rome withdraws from Britain",
	"related_to": [
		{
			"entity": "Rome",
			"relationship_label": "withdraws",
			"relationship_description": "removes legions and authority"
		},
		{
			"entity": "Britain",
			"relationship_label": "is_abandoned",
			"relationship_description": "loses imperial support"
		}
	],
	"updates": {
		"relationship_deletions": [
			{
				"operation": "delete",
				"relation_type": "related_to",
				"target_alias": "Rome"
			}
		]
	}
}
```

## Frontend Implementation Instructions

Build reviewed_pipeline_output in this order:

1. Copy analyze output into reviewed_pipeline_output.outputs.
2. For each proposal item, set status to one of approved, approved_with_updates, or disapproved.
3. Only create updates when user changed something.
4. Never remove base fields when adding updates. Keep original proposal payload and place user changes inside updates.
5. For update_instance entities, always provide an effective target entity_instance_id (directly or in updates).
6. For type moves, set entity_definition_id in updates.
7. For proposal conversion, set proposal_type in updates.
8. For merge operations, include updates.merge and prefer maintained_alias.
9. For scene/milestone relation editing, use updates.related_to replacement or updates.relationship_deletions.
10. If you add explicit IDs with additional_related_entity_instance_ids, ensure they already exist (or are created in Step 1).

Recommended client-side checks before submit:

- status values are valid.
- update_instance entries have a target entity_instance_id.
- all ids are non-empty strings.
- relationship_deletions uses relation_type = related_to.
- payload run_id matches endpoint path run_id.

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

## Route and Task Connection

```
POST /jobs/architect/runs/{run_id}/generate
	→ generate_entities_from_validated_proposals  (architect.py router)
	→ generation_task.delay(...)                  (Celery dispatch)
	→ architect.generate_entities                 (architect_generation.py)
	→ _execute_generation(...)                    (Steps 0-4 + post)
	→ mark_job_done(job_id, result)               (stores reconciliation payload)
```

The route validates that `payload.run_id == path run_id`, fetches the run, then dispatches. Steps 0-4 all run inside `_execute_generation`. There is no partial or optional path — Step 4 always runs for every generation job that reaches that point.

## Job Result and Frontend Reconciliation

When the generate job finishes, `GET /jobs/{generation_job_id}` returns the completed job. The `details` field contains the full reconciliation payload.

`generation_job_id` is available on the run object: `GET /jobs/architect/runs/{run_id}` → `generation_job_id`.

### Polling flow

1. `POST /jobs/architect/runs/{run_id}/generate` → receive `task_id` and `run_id`.
2. `GET /jobs/architect/runs/{run_id}` → read `generation_job_id` (may need one retry if task has not started yet).
3. Poll `GET /jobs/{generation_job_id}` until `status == "done"`.
4. Read `details` from the response for real persisted IDs.

### Completed job response shape

```json
{
	"id": 42,
	"status": "done",
	"job_type": "architect_generation",
	"author_type": "agent",
	"author_id": "agent-uuid",
	"created_at": "2026-04-22T10:00:00Z",
	"updated_at": "2026-04-22T10:01:34Z",
	"details": {
		"run_id": "3f7e2c1a-84b0-4d2e-9f6a-1b2c3d4e5f60",
		"created_entities": 3,
		"updated_entities": 1,
		"created_scenes": 2,
		"created_milestones": 5,
		"impacted_entities": 4,
		"entity_reconciliation": [
			{ "proposal_index": 0, "entity_instance_id": "ent-uuid-aaaa-0001" },
			{ "proposal_index": 1, "entity_instance_id": "ent-uuid-bbbb-0002" },
			{ "proposal_index": 2, "entity_instance_id": "ent-uuid-cccc-0003" },
			{ "proposal_index": 4, "entity_instance_id": "ent-uuid-dddd-0004" }
		],
		"scene_reconciliation": [
			{ "scene_ref": "chunk_0_scene_1", "scene_id": "scene-uuid-1111-aaaa" },
			{ "scene_ref": "chunk_0_scene_2", "scene_id": "scene-uuid-2222-bbbb" }
		],
		"milestone_reconciliation": [
			{ "milestone_ref": "milestone-uuid-m001", "milestone_id": "milestone-uuid-m001" },
			{ "milestone_ref": "milestone-uuid-m002", "milestone_id": "milestone-uuid-m002" },
			{ "milestone_ref": "milestone-uuid-m003", "milestone_id": "milestone-uuid-m003" },
			{ "milestone_ref": "milestone-uuid-m004", "milestone_id": "milestone-uuid-m004" },
			{ "milestone_ref": "milestone-uuid-m005", "milestone_id": "milestone-uuid-m005" }
		]
	}
}
```

### Field reference

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | string | Architect analysis run ID |
| `created_entities` | int | Count of new entities persisted in Step 1 |
| `updated_entities` | int | Count of update_instance targets resolved in Step 1 |
| `created_scenes` | int | Count of scenes persisted in Step 2 |
| `created_milestones` | int | Count of milestones persisted in Step 3 |
| `impacted_entities` | int | Total entity count processed in Step 4 |
| `entity_reconciliation` | array | `{proposal_index, entity_instance_id}` — maps each approved entity proposal to its real graph ID |
| `scene_reconciliation` | array | `{scene_ref, scene_id}` — maps each `scene_ref` from the payload to its real graph ID |
| `milestone_reconciliation` | array | `{milestone_ref, milestone_id}` — maps each `milestone_ref` from the payload to its real graph ID |

`entity_reconciliation` entries are sorted by `proposal_index` ascending. Indices correspond to the zero-based position of the entity in `reviewed_pipeline_output.outputs.entity_proposals`.

`scene_ref` and `milestone_ref` match the values used in the input payload and in `milestones_per_scene[].scene_ref` / `milestones_per_scene[].milestones[].milestone_ref`.

## Notes

- OpenAI configuration is required for enrichment.
- The generate job expects reviewed pipeline output (frontend-curated payload), not raw analyze output.
- Relationship resolution is intentionally strict to avoid unresolved placeholders.
- Scene related_to is now persisted as graph edges (Scene -> Entity RELATES_TO), not only used transiently during generation.
