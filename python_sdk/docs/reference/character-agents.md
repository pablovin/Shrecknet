# CharacterAgentsAPI

CharacterAgent query and administrator embodiment workflow.

## Methods

### `get_model_config(self)`

Read the four model roles from Shrecknet config (administrator only).

### `update_model_config(self, payload)`

Update selected role targets; each supplied target replaces its object.

### `trait_definitions(self)`

No description.

### `list_trait_evidence(self, character_agent_id)`

No description.

### `create(self, payload)`

No description.

### `update(self, character_agent_id, payload)`

No description.

### `list_aspects(self, character_agent_id)`

No description.

### `assign_aspect(self, character_agent_id, payload)`

No description.

### `update_aspect_assignment(self, character_agent_id, aspect_id, payload)`

No description.

### `list_goals(self, character_agent_id)`

No description.

### `pursue_goal(self, character_agent_id, payload)`

No description.

### `update_goal_assignment(self, character_agent_id, goal_id, payload)`

No description.

### `list_revisions(self, character_agent_id)`

No description.

### `list_identity_changes(self, character_agent_id)`

No description.

### `query(self, character_agent_id, payload)`

No description.

### `get_query_job(self, character_agent_id, job_id)`

No description.

### `wait_for_query(self, character_agent_id, job_id)`

No description.

### `start_embodiment(self, payload)`

No description.

### `get_embodiment(self, draft_id)`

No description.

### `delete_embodiment(self, draft_id)`

No description.

### `list_embodiments(self, ontology_id)`

No description.

### `list_perspectives(self, character_agent_id)`

No description.

### `create_perspective(self, character_agent_id, payload)`

No description.

### `get_perspective(self, character_agent_id, perspective_id)`

No description.

### `update_perspective(self, character_agent_id, perspective_id, payload)`

No description.

### `delete_perspective(self, character_agent_id, perspective_id)`

No description.

### `list_emotions(self, character_agent_id, perspective_id)`

No description.

### `create_emotion(self, character_agent_id, perspective_id, payload)`

No description.

### `get_emotion(self, character_agent_id, perspective_id, emotion_id)`

No description.

### `update_emotion(self, character_agent_id, perspective_id, emotion_id, payload)`

No description.

### `delete_emotion(self, character_agent_id, perspective_id, emotion_id)`

No description.

### `list_beliefs(self, character_agent_id, perspective_id)`

No description.

### `create_belief(self, character_agent_id, perspective_id, payload)`

No description.

### `get_belief(self, character_agent_id, perspective_id, belief_id)`

No description.

### `update_belief(self, character_agent_id, perspective_id, belief_id, payload)`

No description.

### `delete_belief(self, character_agent_id, perspective_id, belief_id)`

No description.

### `list_impacts(self, character_agent_id, perspective_id)`

No description.

### `create_impact(self, character_agent_id, perspective_id, payload)`

No description.

### `get_impact(self, character_agent_id, perspective_id, impact_id)`

No description.

### `update_impact(self, character_agent_id, perspective_id, impact_id, payload)`

No description.

### `delete_impact(self, character_agent_id, perspective_id, impact_id)`

No description.

## Weighted profile compatibility

The SDK reads `dispositions-v3-points-perspectives`,
`dispositions-v4-weighted-scenes`, and `dispositions-v5-neutral-prior`. New profile grouping fields are
`observation_groups` and `unresolved_observation_ids`. Evidence adds `source`,
`observation_group_id`, and `independence_status`; old evidence defaults to canonical.
New v5 profiles permit `baseline` for all nine estimates: point 5 with zero
observations and empty observation IDs. Historical v3/v4 permit it only for
Steadiness. Directional smoothing is 6 in v5; historical calculations and separate
Steadiness smoothing 4 are preserved. A baseline does not claim observed direction,
balance, equal action probabilities, or consistency. Upgrade strict clients before deploying new responses.
See [the canonical trait contract](../../../Documentation/Agents/CharacterAgent/Dispositional%20Traits.md).

`observation_group_positions` snapshots the earliest recorded event-group position
across traits, preserving event recency when a stronger retelling becomes the
representative or historical reads select only contributing perspectives.
