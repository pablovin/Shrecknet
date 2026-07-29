"""Six-call atomic CharacterAgent embodiment prompts.

Pipeline per source group:
  Step 1 incorporation -> Step 2 enrichment -> Step 3 observations
                                              /    |     \
                                        Step 4a  4b     4c
                                        axes  aspects  goals

The three Step 4 update calls run in parallel.
"""

PROMPT_VERSION = "character-embodiment-v7-six-call"

PERSPECTIVE_PROMPT = r"""You are incorporating a character's identity into canonical objective scenes.

Scenes are immutable objective evidence and are listed earliest to latest. For
each scene, produce one grounded subjective perspective and one expressive
first-person character_reflection. Never rewrite a misunderstanding, suspicion,
or uncertainty as an objective scene fact.

Return every perspective in the same order as the input scenes, using the exact scene_id for each.

INPUT:
{
  "identity": {
    "alias": "name or alias",
    "subtitle": "subtitle or null",
    "entity_type": "ontology type",
    "entity_type_description": "type description or null",
    "properties": {"property": "value"}
  },
  "current_profile": {
    "behavioural_axes": {"axis_name": value_0_100},
    "aspects": [{"id": "stable id", "name": "...", "category": "...", "description": "..."}],
    "goals": [{"id": "stable id", "title": "...", "description": "...", "goal_type": "..."}]
  },
  "scenes": [
    {"scene_id": "id", "name": "scene name", "description": "scene description", "created_at": "ISO timestamp or null"}
  ],
  "required_output": "<ScenePerspectiveOutput schema for one perspective>"
}

OUTPUT — return an object with exactly one key "perspectives":
{
  "perspectives": [
    {
      "scene_id": "exact input scene_id",
      "source_type": "participated | witnessed | heard_about | read_about | inferred | unknown",
      "awareness_level": 0..100,
      "confidence": 0..100,
      "summary": "concise factual summary",
      "interpretation": "grounded subjective interpretation",
      "character_reflection": "expressive first-person reflection in character voice",
      "memory_strength": 0..100,
      "importance": 1..5
    }
  ]
}

Return JSON only. required_output is authoritative if this description and the schema differ."""


ENRICHMENT_PROMPT = r"""You are enriching grounded scene perspectives with immediate psychological effects.

Canonical scenes remain objective. Interpretations are subjective evidence.
The presentation-only character_reflection is intentionally absent. For every
input scene return exactly one same-order result. Return empty lists when an
effect is not warranted. Do not create or update profile state.

Impacts may target only stable IDs supplied in current_profile. Goal impacts
allow advanced or threatened. Aspect impacts allow created, reinforced, or
invalidated.

INPUT:
{
  "scenes": [{"scene_id":"id","name":"...","description":"...","created_at":null}],
  "perspectives": [{
    "scene_id":"id","source_type":"participated | witnessed | heard_about | read_about | inferred | unknown",
    "awareness_level":0..100,"confidence":0..100,"summary":"...","interpretation":"...",
    "memory_strength":0..100,"importance":1..5
  }],
  "current_profile": {
    "aspects": [{"id":"stable id","name":"..."}],
    "goals": [{"id":"stable id","title":"..."}]
  }
}

OUTPUT:
{
  "scene_enrichments": [{
    "scene_id":"exact input scene_id",
    "emotions":[{"arousal":0..100,"valence":-100..100,"description":"..."}],
    "beliefs":[{"statement":"...","confidence":0..100,"status":"suspected | believed | confirmed | doubted | disproven | superseded"}],
    "impacts":[{"impact_type":"goal_change | aspect_change","target_id":"supplied stable id","direction":"advanced | threatened | created | reinforced | invalidated","magnitude":0..100,"description":"..."}]
  }]
}

Return JSON only. required_output is authoritative if this description and the schema differ."""


OBSERVATIONS_PROMPT = r"""You are distilling character observations from canonical scene bundles.

Given canonical scenes, grounded interpretations, and immediate psychological enrichment, produce grounded
observations. Every observation must cite at least one scene evidence_id from the perspectives.
Expressive character_reflection text is presentation-only and is never supplied as evidence.

EVIDENCE ID FORMAT: use scene:{scene_id} to reference any scene perspective below.

INPUT:
{
  "identity": {"alias":"...","subtitle":null,"entity_type":"...","entity_type_description":null,"properties":{}},
  "scene_bundles": [
    {
      "scene": {"scene_id":"id","name":"...","description":"...","created_at":null},
      "perspective": {
        "scene_id":"id","source_type":"...","awareness_level":0..100,
        "confidence":0..100,"summary":"...","interpretation":"...",
        "memory_strength":0..100,"importance":1..5
      },
      "emotions": [...],
      "beliefs": [...],
      "impacts": [...]
    }
  ],
  "required_output": "<EmbodimentObservationsOutput schema>"
}

OUTPUT:
{
  "recurring_behaviours": [{"text": "grounded statement", "evidence_ids": ["scene:scene_id"]}],
  "motivations": [{"text": "grounded statement", "evidence_ids": ["scene:scene_id"]}],
  "values": [{"text": "grounded statement", "evidence_ids": ["scene:scene_id"]}],
  "fears": [{"text": "grounded statement", "evidence_ids": ["scene:scene_id"]}],
  "conflicts": [{"text": "grounded statement", "evidence_ids": ["scene:scene_id"]}],
  "relationships": [{"text": "person -> relationship description", "evidence_ids": ["scene:scene_id"]}],
  "contradictions": [{"text": "contradicting behaviours or information", "evidence_ids": ["scene:scene_id"]}],
  "evidence_gaps": [{"text": "what is unknown", "evidence_ids": ["scene:scene_id"]}],
  "subtitle_change": {
    "operation": "retain | set | clear",
    "subtitle": "new concise subtitle or null",
    "justification": "why the subtitle should change",
    "confidence": 0.0 to 1.0,
    "evidence_ids": ["scene:scene_id"]
  }
}
subtitle_change is OPTIONAL. Only include it when the scenes clearly warrant a new or cleared subtitle.
When omitted or operation is "retain", the character's subtitle stays unchanged.

Return JSON only. required_output is authoritative if this description and the schema differ."""

AXES_UPDATE_PROMPT = r"""You are updating behavioural axes based on new observations.

Review the current axis values against the observations. Change an axis only when the observations
provide consistent, important evidence that the current value is no longer accurate. An isolated
reaction is not enough. Each axis change must be within ±5 points of the current value — larger
shifts require proportionally stronger evidence across multiple scenes. For unchanged axes, omit
them from the output.

Axis definitions (0=left pole, 50=neutral, 100=right pole):
- calm_aggressive: 0 calm, 100 aggressive
- cautious_reckless: 0 cautious, 100 reckless
- compassionate_ruthless: 0 compassionate, 100 ruthless
- trusting_suspicious: 0 trusting, 100 suspicious
- honest_deceptive: 0 honest, 100 deceptive
- patient_impulsive: 0 patient, 100 impulsive
- humble_proud: 0 humble, 100 proud
- cooperative_dominating: 0 cooperative, 100 dominating

INPUT:
{
  "current_axes": {"axis_name": value_0_100},
  "observations": {
    "recurring_behaviours": [...],
    "motivations": [...],
    "values": [...],
    "fears": [...],
    "conflicts": [...],
    "relationships": [...],
    "contradictions": [...],
    "evidence_gaps": [...]
  },
  "required_output": "<AxisChangeOutput schema>"
}

OUTPUT — return only the axes that should change:
{
  "behavioural_axes": [
    {
      "axis": "axis_name",
      "new_value": 0..100,
      "justification": "why this value changed",
      "confidence": 0..1,
      "evidence_ids": ["evidence_id from observations"]
    }
  ]
}
Include only axes that actually change. Return JSON only."""

ASPECTS_UPDATE_PROMPT = r"""You are updating character aspects based on new observations.

Review the current aspects against the observations. Default to no change unless observations
clearly support adding a new stable aspect, updating an existing one, or removing one that has
been invalidated.

An aspect is a stable, high-impact identity fact, role, state, physical characteristic, capability,
knowledge, preference, attitude, or history.

INPUT:
{
  "current_aspects": [
    {"name": "aspect name", "category": "category", "description": "description or null", "importance": 1..5, "intensity": 0..100 or null}
  ],
  "observations": {
    "recurring_behaviours": [...],
    "motivations": [...],
    "values": [...],
    "fears": [...],
    "conflicts": [...],
    "relationships": [...],
    "contradictions": [...],
    "evidence_gaps": [...]
  },
  "required_output": "<AspectUpdateOutput schema>"
}

OUTPUT — return aspect update operations:
{
  "aspect_updates": [
    {
      "operation": "add | update | remove",
      "name": "aspect name",
      "category": "identity | role | status | physical | capability | knowledge | preference | attitude | history",
      "description": "description or null",
      "importance": 1..5 or null,
      "intensity": 0..100 or null,
      "justification": "why this operation",
      "confidence": 0..1,
      "evidence_ids": ["evidence_id from observations"]
    }
  ]
}
For remove, name and justification are required; all other fields are ignored.
Return JSON only."""

GOALS_UPDATE_PROMPT = r"""You are updating character goals based on new observations.

Review the current goals against the observations. Default to no change unless observations
clearly support adding a new persistent goal, updating an existing one, marking one as complete,
or removing one that has been abandoned or superseded.

A goal is an active persistent driver, not a completed one-time action.

INPUT:
{
  "current_goals": [
    {"title": "goal title", "description": "description", "goal_type": "type", "priority": 0..100, "commitment": 0..100}
  ],
  "observations": {
    "recurring_behaviours": [...],
    "motivations": [...],
    "values": [...],
    "fears": [...],
    "conflicts": [...],
    "relationships": [...],
    "contradictions": [...],
    "evidence_gaps": [...]
  },
  "required_output": "<GoalUpdateOutput schema>"
}

OUTPUT — return goal update operations:
{
  "goal_updates": [
    {
      "operation": "add | update | remove | complete",
      "title": "goal title",
      "description": "description or null",
      "goal_type": "desire | objective | ambition | obligation | avoidance | survival",
      "priority": 0..100 or null,
      "commitment": 0..100 or null,
      "basis": "explicit | inferred",
      "justification": "why this operation",
      "confidence": 0..1,
      "evidence_ids": ["evidence_id from observations"]
    }
  ]
}
For remove and complete, title and justification are required; all other fields are ignored.
Return JSON only."""
