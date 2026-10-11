# Scene and Milestone Endpoints for Frontend

Base path scope:
- `/ontology-instances/{instance_id}/scenes`

Auth:
- Bearer token required

## Scenes

### List scenes
- `GET /ontology-instances/{instance_id}/scenes`

Example:
```http
GET /ontology-instances/inst_123/scenes
Authorization: Bearer <token>
```

### Create scene
- `POST /ontology-instances/{instance_id}/scenes`

Example:
```json
{
  "id": "scene_opening",
  "name": "Opening at the Marsh Gate",
  "description": "The party meets the warden.",
  "created_by_type": "human",
  "created_by_author": "user_42",
  "derived_from": { "entity_instance_id": "entity_warden" },
  "milestones": [
    {
      "id": "ms_begin",
      "name": "Scene starts",
      "description": "Initial approach",
      "created_by_type": "human",
      "created_by_author": "user_42",
      "temporal_type": "beginning",
      "boundary_type": "begin",
      "derived_from": { "entity_instance_id": "entity_warden" }
    },
    {
      "id": "ms_end",
      "name": "Scene ends",
      "description": "Agreement reached",
      "created_by_type": "human",
      "created_by_author": "user_42",
      "temporal_type": "ending",
      "boundary_type": "end",
      "derived_from": { "entity_instance_id": "entity_party" }
    }
  ]
}
```

### Get scene
- `GET /ontology-instances/{instance_id}/scenes/{scene_id}`

### Update scene
- `PUT /ontology-instances/{instance_id}/scenes/{scene_id}`

### Delete scene
- `DELETE /ontology-instances/{instance_id}/scenes/{scene_id}`

## Milestones

Base under scene:
- `/ontology-instances/{instance_id}/scenes/{scene_id}/milestones`

### List milestones
- `GET /ontology-instances/{instance_id}/scenes/{scene_id}/milestones`

### Create milestone
- `POST /ontology-instances/{instance_id}/scenes/{scene_id}/milestones`

Example:
```json
{
  "id": "ms_clue",
  "name": "Clue revealed",
  "description": "A hidden sigil is found",
  "created_by_type": "human",
  "created_by_author": "user_42",
  "temporal_type": "other",
  "boundary_type": "none",
  "derived_from": { "entity_instance_id": "entity_detective" },
  "entity_relations": [
    { "entity_instance_id": "entity_detective", "label": "investigates" },
    { "entity_instance_id": "entity_sigil", "label": "discovers" }
  ]
}
```

### Get milestone
- `GET /ontology-instances/{instance_id}/scenes/{scene_id}/milestones/{milestone_id}`

### Update milestone
- `PUT /ontology-instances/{instance_id}/scenes/{scene_id}/milestones/{milestone_id}`

### Delete milestone
- `DELETE /ontology-instances/{instance_id}/scenes/{scene_id}/milestones/{milestone_id}`

## Event Write Deprecation (important for frontend)

Legacy event write endpoints are blocked and return conflict/deprecation responses. Use Scene and Milestone write endpoints for new UI flows.

## Trait observation event provenance

Scene create/update/read payloads at `/ontology-instances/{instance_id}/scenes`
and `/ontology-instances/{instance_id}/scenes/{scene_id}` additionally support:

```json
{"observation_group_id":"reviewed-event-42","independence_status":"independent"}
```

`observation_group_id` is nullable, nonblank when present, and at most 255 characters.
An explicit null in a scene update clears it; omission preserves it.
`independence_status` is `independent` or `unresolved`, defaulting to independent
on creation/legacy reads; omission or null in updates preserves it. Existing
scene authorization and instance isolation apply. Assign shared IDs to reviewed
retellings; shared source entities alone do not prove a shared event. Unresolved
observations do not contribute to CharacterAgent trait/Steadiness support.
Changing provenance on processed scenes requires explicit regeneration, with no
automatic historical rewrite. See [the canonical trait contract](../../Agents/CharacterAgent/Dispositional%20Traits.md#independent-event-grouping).
