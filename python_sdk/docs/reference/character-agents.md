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
